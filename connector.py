"""MySQL connector — dialect + connector class for the Analitiq CDK.

Everything MySQL-specific lives here, in the connector package: backtick
identifier quoting, the system-schema exclusion list, the ``ON DUPLICATE
KEY UPDATE`` upsert statement, the MySQL-native TLS-mode interpretation,
and the structural DDL overrides (``CURRENT_TIMESTAMP(6)`` defaults,
bounded ``VARCHAR(255)`` batch-commit keys). MySQL runs on the async
SQLAlchemy transport (``mysql+aiomysql``) — no first-class ADBC driver
exists for MySQL — so the ADBC hooks stay on the neutral base.

The write-direction type vocabulary is declarative-only: DDL column types
render through ``definition/type-map-write.json`` via the base
``render_column_type``. No Python type-rendering table ships here.

Registered under connector_id ``mysql`` via the package entry points
(``analitiq.source_connectors`` / ``analitiq.destination_connectors``).
"""

from __future__ import annotations

import ssl as _ssl
from typing import Any, Dict, List

from sqlalchemy.dialects.mysql import insert as mysql_insert

from cdk.sql.dialects import SqlDialect
from cdk.sql.generic import GenericSQLConnector
from cdk.transport_factory import ca_ssl_context


class MySQLDialect(SqlDialect):
    """MySQL SQL strategy: a schema is a database; identifiers use backticks."""

    name = "mysql"
    quote_char = "`"
    system_schemas = ("information_schema", "mysql", "performance_schema", "sys")
    supports_upsert_sqlalchemy = True

    # ---- SQLAlchemy write path ---------------------------------------------
    def build_sqlalchemy_upsert(
        self,
        table: Any,
        records: List[Dict[str, Any]],
        conflict_keys: List[str],
    ) -> Any:
        stmt = mysql_insert(table).values(records)
        record_columns = set(records[0].keys())
        update_cols = {
            c.name: c
            for c in stmt.inserted
            if c.name not in conflict_keys and c.name in record_columns
        }
        return stmt.on_duplicate_key_update(**update_cols)

    # ---- structural overrides (the portable form is invalid on MySQL) ------
    def current_timestamp_default(self) -> str:
        # MySQL requires the fractional-seconds precision (fsp) of a DEFAULT
        # expression to match the column's fsp exactly; a mismatch is error
        # 1067 "Invalid default value". The write map renders Timestamp
        # canonicals as DATETIME(6) (definition/type-map-write.json), so the
        # default must be CURRENT_TIMESTAMP(6).
        return "CURRENT_TIMESTAMP(6)"

    def batch_commits_key_type(self, type_mapper) -> str:
        # TEXT (the write map's Utf8 render) cannot be a MySQL primary key
        # without a prefix length; the engine's idempotency keys are bounded
        # identifiers, so a bounded VARCHAR is correct.
        return "VARCHAR(255)"

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
            # Negotiate TLS without verifying the server certificate (no CA
            # bundle shipped). ``check_hostname`` must be False whenever
            # ``verify_mode`` is CERT_NONE or CPython raises. aiomysql cannot
            # express PREFERRED's plaintext fallback; both modes attempt TLS.
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            return ctx
        if canonical == "VERIFY_CA":
            if not ca_pem:
                raise ValueError(
                    "tls.mode='VERIFY_CA' requires tls.ca_certificate to "
                    "resolve to a PEM certificate bundle"
                )
            return ca_ssl_context(ca_pem, check_hostname=False)
        if canonical == "VERIFY_IDENTITY":
            if not ca_pem:
                raise ValueError(
                    "tls.mode='VERIFY_IDENTITY' requires tls.ca_certificate "
                    "to resolve to a PEM certificate bundle"
                )
            return ca_ssl_context(ca_pem, check_hostname=True)
        raise ValueError(
            f"{self.name} tls.mode {mode!r} not recognized; expected one of: "
            "DISABLED, PREFERRED, REQUIRED, VERIFY_CA, VERIFY_IDENTITY"
        )


class MySQLConnector(GenericSQLConnector):
    """MySQL connector: the CDK SQL base wired to the MySQL dialect.

    Writes ride the generic batched-INSERT path. MySQL's native bulk-load
    protocol (``LOAD DATA LOCAL INFILE``) requires ``local_infile`` to be
    enabled on BOTH server and client — it is disabled by default on MySQL
    8.0 servers — so the batched path is the portable default.
    """

    dialect_class = MySQLDialect
