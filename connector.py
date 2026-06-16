"""MySQL connector — dialect + connector class for the Analitiq CDK.

Everything MySQL-specific lives here, in the connector package: backtick
identifier quoting, the system-schema list, and the ``ON DUPLICATE KEY
UPDATE`` upsert statement. MySQL runs on the SQLAlchemy transport only
(no ADBC driver), so the ADBC hooks stay on the neutral base.

Registered under connector_id ``mysql`` via the package entry points.
"""

from __future__ import annotations

import ssl as _ssl

from typing import Any, Dict, List

from sqlalchemy.dialects.mysql import insert as mysql_insert

from cdk.sql.dialects import SqlDialect
from cdk.transport_factory import ca_ssl_context
from cdk.sql.generic import GenericSQLConnector


class MySQLDialect(SqlDialect):
    """MySQL SQL strategy: a schema is a database; identifiers use backticks."""

    name = "mysql"
    quote_char = "`"
    system_schemas = ("information_schema", "mysql", "performance_schema", "sys")
    supports_upsert_sqlalchemy = True

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

    def batch_commits_key_type(self, type_mapper) -> str:
        # TEXT (the write map's Utf8) cannot be a primary key on
        # MySQL/MariaDB without a prefix length; the engine's idempotency
        # keys are bounded identifiers, so a bounded VARCHAR is correct.
        return "VARCHAR(255)"

    def current_timestamp_default(self) -> str:
        # MySQL requires the fsp in DEFAULT to match the column's fsp; DATETIME(6)
        # DEFAULT CURRENT_TIMESTAMP (no precision) is rejected with error 1067.
        return "CURRENT_TIMESTAMP(6)"

    def build_tls_connect_arg(self, mode: str, ca_pem: str | None) -> Any:
        """MySQL-native SSL modes for aiomysql.

        aiomysql accepts ``False`` (no TLS) or an SSLContext — never the
        native mode strings. Vocabulary: ``DISABLED`` / ``PREFERRED`` /
        ``REQUIRED`` / ``VERIFY_CA`` / ``VERIFY_IDENTITY``
        (case-insensitive on the stored value).
        """
        canonical = mode.upper()
        if canonical == "DISABLED":
            return False
        if canonical in ("PREFERRED", "REQUIRED"):
            # Negotiate TLS without verifying the server certificate (the
            # connection didn't ship a CA bundle). ``check_hostname`` must
            # be False whenever ``verify_mode`` is CERT_NONE or CPython
            # raises.
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
    """MySQL connector: the CDK SQL base wired to the MySQL dialect."""

    dialect_class = MySQLDialect
