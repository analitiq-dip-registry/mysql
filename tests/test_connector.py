"""Unit tests for MySQLDialect.verify_tls_state (post-connect TLS probe).

The hook receives a raw DBAPI connection (for async drivers, SQLAlchemy's
asyncio adapter exposing the same cursor surface) and must raise
TlsVerificationError when a TLS-promising mode finds an unencrypted session.
"""

import pytest
from unittest.mock import MagicMock

from cdk.sql.exceptions import TlsVerificationError  # stubbed in conftest
from connector import MySQLDialect


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
