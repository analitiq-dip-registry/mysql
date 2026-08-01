"""MySQL connector — dialect + connector class for the Analitiq CDK.

Everything MySQL-specific lives here, in the connector package: backtick
identifier quoting, the system-schema exclusion list, the stage-then-merge
write renderings (``CREATE [TEMPORARY] TABLE ... LIKE ...`` staging plus the
``INSERT ... SELECT ... ON DUPLICATE KEY UPDATE`` merge), the MySQL-native
TLS-mode interpretation, and the ``CURRENT_TIMESTAMP(6)`` structural DDL
default. MySQL runs on the async SQLAlchemy transport (``mysql+aiomysql``)
— no first-class ADBC driver exists for MySQL.

The write-direction type vocabulary is declarative-only: DDL column types
render through ``definition/type-map-write.json`` via the base
``render_column_type``. No Python type-rendering table ships here.

Registered under connector_id ``mysql`` via the package entry points
(``analitiq.source_connectors`` / ``analitiq.destination_connectors``).
"""

from __future__ import annotations

import ssl as _ssl
from collections.abc import Sequence
from typing import Any

from cdk.sql.dialects import SqlDialect, TableAddress
from cdk.sql.exceptions import TlsVerificationError
from cdk.sql.generic import GenericSQLConnector
from cdk.transport_factory import ca_ssl_context


class MySQLDialect(SqlDialect):
    """MySQL SQL strategy: a schema is a database; identifiers use backticks."""

    name = "mysql"
    quote_char = "`"
    system_schemas = ("information_schema", "mysql", "performance_schema", "sys")

    # ---- stage-then-merge write path (ADR sql-write-path-v2) ---------------
    def stage_table_sql(
        self, stage: TableAddress, target: TableAddress, *, temp: bool
    ) -> str:
        """``CREATE [TEMPORARY] TABLE`` *stage* shaped like *target*.

        Every SQL write lands in a stage table first. MySQL copies a table's
        full definition with the unparenthesized ``LIKE`` form (Postgres uses
        ``(LIKE ... INCLUDING DEFAULTS)``). The connector declares temp-scope
        staging (``sql_capabilities.stage.scope == "temp"``), so ``temp`` is
        True: a session-scoped temporary table that carries no schema.
        """
        keyword = "CREATE TEMPORARY TABLE" if temp else "CREATE TABLE"
        return f"{keyword} {self.quote_table(stage)} LIKE {self.quote_table(target)}"

    def merge_statement_sql(
        self,
        stage: TableAddress,
        target: TableAddress,
        conflict_keys: Sequence[str],
        columns: Sequence[str],
    ) -> str:
        """Render MySQL's declared merge form: ``INSERT ... ON DUPLICATE KEY``.

        The ``insert_on_duplicate_key`` form names no match keys in the
        statement — MySQL reads them from whichever unique index the conflict
        lands on — so ``conflict_keys`` only selects which landed columns are
        updated (``columns`` minus the keys). Columns the target has but the
        batch did not land keep their stored value on matched rows and their
        DEFAULT on inserted ones.
        """
        column_list = ", ".join(self.quote_ident(c) for c in columns)
        update_columns = [c for c in columns if c not in set(conflict_keys)]
        statement = (
            f"INSERT INTO {self.quote_table(target)} ({column_list}) "  # nosec B608
            f"SELECT {column_list} FROM {self.quote_table(stage)} "
        )
        if not update_columns:
            # Every landed column is a conflict key (e.g. a junction table):
            # there is nothing to update. MySQL rejects an empty ON DUPLICATE
            # KEY UPDATE clause, so render the documented self-assignment
            # no-op (``col = col``) on one key column — matched rows keep
            # their stored values, never an error. This is MySQL's
            # insert-only degradation, equivalent to Postgres
            # ``ON CONFLICT DO NOTHING``.
            key = self.quote_ident(conflict_keys[0])
            return statement + f"ON DUPLICATE KEY UPDATE {key} = {key}"
        # ``VALUES(col)`` — the value that would have been inserted — is the
        # portable reference for the incoming row across MySQL 5.7/8.0 and
        # MariaDB (which ships its own copy of this dialect and does not
        # support the 8.0.19 ``AS alias`` row-alias form). VALUES() is
        # deprecated (warning only) on MySQL 8.0.20+, not removed.
        assignments = ", ".join(
            f"{self.quote_ident(c)} = VALUES({self.quote_ident(c)})"
            for c in update_columns
        )
        return statement + f"ON DUPLICATE KEY UPDATE {assignments}"

    # ---- structural override (the portable form is invalid on MySQL) -------
    def current_timestamp_default(self) -> str:
        # MySQL requires the fractional-seconds precision (fsp) of a DEFAULT
        # expression to match the column's fsp exactly; a mismatch is error
        # 1067 "Invalid default value". The write map renders Timestamp
        # canonicals as DATETIME(6) (definition/type-map-write.json), so the
        # default must be CURRENT_TIMESTAMP(6).
        return "CURRENT_TIMESTAMP(6)"

    # ---- session -----------------------------------------------------------
    def session_init_sql(self) -> list[str]:
        # MySQL stores TIMESTAMP values as UTC but converts them through the
        # session time_zone on retrieval, so the read map's tz-aware
        # Timestamp(<unit>, UTC) canonicals carry correct instants only when
        # the session runs in UTC. The CDK executes these statements on every
        # new connection (after verify_tls_state, before use), which pins the
        # conversion regardless of the server's global time_zone. DATETIME is
        # zoneless on the wire and unaffected.
        return ["SET time_zone = '+00:00'"]

    # ---- TLS ---------------------------------------------------------------
    def build_tls_connect_arg(self, mode: str, ca_pem: str | None) -> Any:
        """Interpret the connector's MySQL-native ``ssl_mode`` vocabulary.

        aiomysql accepts ``False`` (no TLS) or an ``ssl.SSLContext`` — never
        the native mode strings. Vocabulary (MySQL ``--ssl-mode``):
        ``DISABLED`` / ``PREFERRED`` / ``REQUIRED`` / ``VERIFY_CA`` /
        ``VERIFY_IDENTITY`` (tolerated case-insensitively on the stored
        value).
        """
        canonical = mode.upper()
        if canonical == "DISABLED":
            return False
        if canonical in ("PREFERRED", "REQUIRED"):
            # TLS without server-certificate verification (any supplied CA is
            # ignored in these modes, per --ssl-mode semantics).
            # ``check_hostname`` must be False whenever ``verify_mode`` is
            # CERT_NONE or CPython raises. aiomysql performs the TLS
            # handshake only when the server advertises the SSL capability
            # and silently proceeds in plaintext otherwise, so an SSLContext
            # delivers PREFERRED semantics at this layer; REQUIRED's
            # no-fallback guarantee is enforced post-connect by
            # ``verify_tls_state`` below, which the CDK invokes on every new
            # pooled connection.
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            return ctx
        if canonical == "VERIFY_CA":
            if not ca_pem:
                raise ValueError(
                    "ssl_mode='VERIFY_CA' requires an SSL CA Certificate "
                    "(ssl_ca_certificate): provide a PEM-encoded CA bundle"
                )
            return ca_ssl_context(ca_pem, check_hostname=False)
        if canonical == "VERIFY_IDENTITY":
            if not ca_pem:
                raise ValueError(
                    "ssl_mode='VERIFY_IDENTITY' requires an SSL CA "
                    "Certificate (ssl_ca_certificate): provide a PEM-encoded "
                    "CA bundle"
                )
            return ca_ssl_context(ca_pem, check_hostname=True)
        raise ValueError(
            f"ssl_mode {mode!r} not recognized; expected one of: "
            "DISABLED, PREFERRED, REQUIRED, VERIFY_CA, VERIFY_IDENTITY"
        )

    def verify_tls_state(self, dbapi_connection: Any, mode: str) -> None:
        """Fail closed when a TLS-promising mode landed on a plaintext session.

        aiomysql performs the TLS handshake only when the server advertises
        the SSL capability and silently proceeds in plaintext otherwise, so
        connect arguments alone cannot enforce ``REQUIRED`` / ``VERIFY_CA``
        / ``VERIFY_IDENTITY`` — an active MITM can strip the capability
        flag and downgrade the strictest mode to plaintext. The CDK calls
        this hook on every new pooled DBAPI connection whenever the
        transport declares a TLS mode; an empty ``Ssl_cipher`` status value
        means the session is not encrypted. ``DISABLED`` and ``PREFERRED``
        do not promise encryption and pass without probing.
        """
        canonical = mode.upper()
        if canonical not in ("REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"):
            return
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SHOW STATUS LIKE 'Ssl_cipher'")
            row = cursor.fetchone()
        finally:
            cursor.close()
        cipher = (row[1] if row else None) or ""
        if not cipher.strip():
            raise TlsVerificationError(
                f"ssl_mode={mode!r} requires an encrypted connection, but "
                "the established session is not encrypted (empty Ssl_cipher "
                "status). The MySQL server does not have TLS enabled, or an "
                "active attacker stripped the SSL capability from the "
                "handshake. Enable TLS on the server, or choose ssl_mode "
                "DISABLED/PREFERRED if plaintext is acceptable."
            )


class MySQLConnector(GenericSQLConnector):
    """MySQL connector: the CDK SQL base wired to the MySQL dialect.

    Writes ride the shared stage-then-merge primitive: rows land into a
    session temp stage via executemany, then one
    ``INSERT ... SELECT ... ON DUPLICATE KEY UPDATE`` mode statement applies
    them. MySQL's native ``LOAD DATA LOCAL INFILE`` bulk path is not wired —
    it needs ``local_infile`` enabled on BOTH server and client (disabled by
    default on MySQL 8.0 servers) — so ``bulk_load`` is declared as ``{}``
    (no MySQL-specific mechanism) and batch landing uses the CDK's default
    executemany-based path. ``empty_table_sql`` and ``bulk_land`` are
    likewise not overridden: the CDK base-class defaults (``TRUNCATE TABLE``
    rendered via the dialect's own quoting, and the executemany landing path)
    are correct for MySQL without modification.
    """

    dialect_class = MySQLDialect
