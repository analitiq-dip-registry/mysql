"""Unit tests for MySQLDialect.

Covers the stage-then-merge write-path renderers (stage_table_sql,
merge_statement_sql — including the conformance-required all-keys no-op
degradation), verify_tls_state (post-connect TLS probe), and
session_init_sql (session time_zone pinning).

The TLS hook receives a raw DBAPI connection (for async drivers, SQLAlchemy's
asyncio adapter exposing the same cursor surface) and must raise
TlsVerificationError when a TLS-promising mode finds an unencrypted session.
"""

import json
import re
from pathlib import Path

import pytest
from unittest.mock import MagicMock

from cdk.sql.dialects import TableAddress  # stubbed in conftest
from cdk.sql.exceptions import TlsVerificationError  # stubbed in conftest
from connector import MySQLDialect

_DEFINITION_DIR = Path(__file__).resolve().parent.parent / "definition"


def _make_dbapi_connection(cipher) -> MagicMock:
    """Mock DBAPI connection answering SHOW STATUS LIKE 'Ssl_cipher'."""
    conn = MagicMock()
    cursor = conn.cursor.return_value
    # row[0] = Variable_name, row[1] = Value
    cursor.fetchone.return_value = ("Ssl_cipher", cipher)
    return conn


class TestVerifyTlsState:
    def setup_method(self):
        self.dialect = MySQLDialect()

    # ------------------------------------------------------------------
    # Modes that must never query the server
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("mode", ["DISABLED", "disabled", "PREFERRED", "preferred"])
    def test_no_op_for_non_strict_modes(self, mode):
        conn = MagicMock()
        self.dialect.verify_tls_state(conn, mode)
        conn.cursor.assert_not_called()

    def test_no_op_for_unknown_mode(self):
        """Unrecognized modes are a no-op; mode validation is the caller's job."""
        conn = MagicMock()
        self.dialect.verify_tls_state(conn, "BOGUS_MODE")
        conn.cursor.assert_not_called()

    # ------------------------------------------------------------------
    # Strict modes with an active cipher — must not raise
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("mode", ["REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"])
    def test_passes_when_encrypted(self, mode):
        conn = _make_dbapi_connection("AES128-SHA256")
        self.dialect.verify_tls_state(conn, mode)  # should not raise

    @pytest.mark.parametrize("mode", ["required", "verify_ca", "verify_identity"])
    def test_case_insensitive_strict_modes(self, mode):
        conn = _make_dbapi_connection("TLS_AES_256_GCM_SHA384")
        self.dialect.verify_tls_state(conn, mode)  # should not raise

    # ------------------------------------------------------------------
    # Strict modes with no cipher — must raise TlsVerificationError
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("mode", ["REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"])
    def test_raises_when_not_encrypted(self, mode):
        conn = _make_dbapi_connection("")
        with pytest.raises(TlsVerificationError, match="ssl_mode="):
            self.dialect.verify_tls_state(conn, mode)

    @pytest.mark.parametrize("cipher", [" ", "  \t  "])
    def test_raises_when_cipher_is_whitespace_only(self, cipher):
        conn = _make_dbapi_connection(cipher)
        with pytest.raises(TlsVerificationError):
            self.dialect.verify_tls_state(conn, "REQUIRED")

    def test_raises_when_cipher_value_is_null(self):
        """row[1] = None (DB NULL) is treated as unencrypted."""
        conn = _make_dbapi_connection(None)
        with pytest.raises(TlsVerificationError):
            self.dialect.verify_tls_state(conn, "REQUIRED")

    def test_raises_when_status_row_missing(self):
        """No row from SHOW STATUS at all is treated as unencrypted."""
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = None
        with pytest.raises(TlsVerificationError):
            self.dialect.verify_tls_state(conn, "REQUIRED")

    def test_error_message_names_the_mode(self):
        conn = _make_dbapi_connection("")
        with pytest.raises(TlsVerificationError, match="'REQUIRED'"):
            self.dialect.verify_tls_state(conn, "REQUIRED")

    # ------------------------------------------------------------------
    # DBAPI cursor discipline
    # ------------------------------------------------------------------

    def test_executes_show_status_via_dbapi_cursor(self):
        conn = _make_dbapi_connection("AES128-SHA256")
        self.dialect.verify_tls_state(conn, "REQUIRED")
        cursor = conn.cursor.return_value
        cursor.execute.assert_called_once_with("SHOW STATUS LIKE 'Ssl_cipher'")
        cursor.close.assert_called_once()

    def test_cursor_closed_even_when_probe_raises(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.execute.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            self.dialect.verify_tls_state(conn, "REQUIRED")
        cursor.close.assert_called_once()


class TestSessionInitSql:
    def test_pins_session_time_zone_to_utc(self):
        assert MySQLDialect().session_init_sql() == ["SET time_zone = '+00:00'"]

    def test_is_deterministic(self):
        dialect = MySQLDialect()
        assert dialect.session_init_sql() == dialect.session_init_sql()


class TestStageTableSql:
    def setup_method(self):
        self.dialect = MySQLDialect()
        self.stage = TableAddress(table="_stage_orders", schema="shop")
        self.target = TableAddress(table="orders", schema="shop")

    def test_temp_stage_renders_create_temporary_table_like(self):
        sql = self.dialect.stage_table_sql(self.stage, self.target, temp=True)
        assert sql == (
            "CREATE TEMPORARY TABLE `shop`.`_stage_orders` LIKE `shop`.`orders`"
        )

    def test_non_temp_stage_omits_temporary_keyword(self):
        sql = self.dialect.stage_table_sql(self.stage, self.target, temp=False)
        assert sql.startswith("CREATE TABLE ")
        assert "TEMPORARY" not in sql

    def test_uses_unparenthesized_like(self):
        # MySQL copies the table shape with bare `LIKE target`, never the
        # Postgres `(LIKE ... INCLUDING ...)` parenthesized form.
        sql = self.dialect.stage_table_sql(self.stage, self.target, temp=True)
        assert " LIKE `shop`.`orders`" in sql
        assert "(LIKE" not in sql


class TestEmptyTableSql:
    """Confirm the conftest stub mirrors CDK base behavior for empty_table_sql.

    MySQLDialect does not override empty_table_sql — the CDK base class
    renders TRUNCATE TABLE via the dialect's own quote_table method. The
    conftest stub mirrors that implementation so the rendering tests below
    verify the stub produces correct MySQL SQL. test_no_override_needed is
    the load-bearing assertion: it proves MySQLDialect inherits the hook
    rather than shadowing it, so the real CDK base runs in production.
    """

    def setup_method(self):
        self.dialect = MySQLDialect()
        self.table = TableAddress(table="orders", schema="shop")

    def test_renders_truncate_table(self):
        sql = self.dialect.empty_table_sql(self.table)
        assert sql == "TRUNCATE TABLE `shop`.`orders`"

    def test_reserved_word_table_name_is_quoted(self):
        # MySQL reserved words (e.g. 'order') require backtick quoting to be
        # valid SQL; this pins the quoting contract for the inherited method.
        table = TableAddress(table="order", schema="shop")
        sql = self.dialect.empty_table_sql(table)
        assert sql == "TRUNCATE TABLE `shop`.`order`"

    def test_unqualified_table(self):
        table = TableAddress(table="orders")
        sql = self.dialect.empty_table_sql(table)
        assert sql == "TRUNCATE TABLE `orders`"

    def test_no_override_needed(self):
        # MySQLDialect must not shadow empty_table_sql; the CDK base handles it.
        assert "empty_table_sql" not in MySQLDialect.__dict__


class TestMergeStatementSql:
    def setup_method(self):
        self.dialect = MySQLDialect()
        self.stage = TableAddress(table="_stage_orders", schema="shop")
        self.target = TableAddress(table="orders", schema="shop")

    def test_renders_insert_select_on_duplicate_key_update(self):
        sql = self.dialect.merge_statement_sql(
            self.stage, self.target,
            conflict_keys=["id"], columns=["id", "total", "status"],
        )
        assert sql == (
            "INSERT INTO `shop`.`orders` (`id`, `total`, `status`) "
            "SELECT `id`, `total`, `status` FROM `shop`.`_stage_orders` "
            "ON DUPLICATE KEY UPDATE `total` = VALUES(`total`), "
            "`status` = VALUES(`status`)"
        )

    def test_update_set_excludes_conflict_keys(self):
        sql = self.dialect.merge_statement_sql(
            self.stage, self.target,
            conflict_keys=["id"], columns=["id", "total"],
        )
        # `id` is a conflict key → never appears in the UPDATE clause.
        update_clause = sql.split("ON DUPLICATE KEY UPDATE", 1)[1]
        assert "`id` =" not in update_clause
        assert "`total` = VALUES(`total`)" in update_clause

    def test_all_key_columns_degrades_to_self_assignment_noop(self):
        # Conformance-required: when every landed column is a conflict key,
        # an empty ON DUPLICATE KEY UPDATE clause is invalid SQL. The
        # renderer must emit a self-assignment no-op instead.
        sql = self.dialect.merge_statement_sql(
            self.stage, self.target,
            conflict_keys=["id"], columns=["id"],
        )
        assert not sql.rstrip().endswith("ON DUPLICATE KEY UPDATE")
        assert sql.endswith("ON DUPLICATE KEY UPDATE `id` = `id`")

    def test_composite_all_key_columns_self_assigns_one_key(self):
        sql = self.dialect.merge_statement_sql(
            self.stage, self.target,
            conflict_keys=["a", "b"], columns=["a", "b"],
        )
        assert sql.endswith("ON DUPLICATE KEY UPDATE `a` = `a`")

    def test_no_foreign_merge_tokens_leak_into_sql(self):
        # test_merge_statement_matches_declared_form asserts the rendered SQL
        # carries ON DUPLICATE KEY UPDATE and never MERGE or ON CONFLICT.
        for cols, keys in ([["id", "v"], ["id"]], [["id"], ["id"]]):
            sql = self.dialect.merge_statement_sql(
                self.stage, self.target, conflict_keys=keys, columns=cols,
            )
            assert re.search(r"\bON\s+DUPLICATE\s+KEY\s+UPDATE\b", sql, re.I)
            assert not re.search(r"\bMERGE\b", sql, re.I)
            assert not re.search(r"\bON\s+CONFLICT\b", sql, re.I)


class TestTypeMapWrite:
    """Structural validation of definition/type-map-write.json.

    The rc17 contract does not permit 'Object', 'List'/'LargeList', or
    'Struct<T>' as write-map canonicals — those identifiers are only valid as
    read-map narrowings of a Json canonical.  Rules carrying them are dead (the
    engine rejects them before evaluation) and must not appear in the write map.
    A companion assertion verifies that exactly one valid 'Json → JSON' rule is
    retained, guarding against accidentally removing it alongside the dead ones.
    """

    def setup_method(self):
        with open(_DEFINITION_DIR / "type-map-write.json") as f:
            self._rules = json.load(f)

    def _canonicals(self):
        return [r["canonical"] for r in self._rules]

    def _stripped(self, canonical):
        # Strip regex escape sequences (e.g. \( \) \s) before substring checks
        # so metacharacter escapes are not confused with type-name occurrences.
        return re.sub(r"\\.", "", canonical)

    def test_no_object_canonical(self):
        for canonical in self._canonicals():
            assert "Object" not in self._stripped(canonical), (
                f"Write map must not carry an 'Object' canonical: {canonical!r}"
            )

    def test_no_list_canonical(self):
        for canonical in self._canonicals():
            assert "List" not in self._stripped(canonical), (
                f"Write map must not carry a 'List' canonical: {canonical!r}"
            )

    def test_no_struct_canonical(self):
        for canonical in self._canonicals():
            assert "Struct" not in self._stripped(canonical), (
                f"Write map must not carry a 'Struct' canonical: {canonical!r}"
            )

    def test_json_canonical_maps_to_json_native(self):
        json_rules = [r for r in self._rules if r.get("canonical") == "Json"]
        assert len(json_rules) == 1, "Expected exactly one Json → JSON rule"
        assert json_rules[0]["native"] == "JSON"
