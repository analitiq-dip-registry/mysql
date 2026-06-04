"""MySQL connector — dialect + connector class for the Analitiq CDK.

Everything MySQL-specific lives here, in the connector package: backtick
identifier quoting, the system-schema list, and the ``ON DUPLICATE KEY
UPDATE`` upsert statement. MySQL runs on the SQLAlchemy transport only
(no ADBC driver), so the ADBC hooks stay on the neutral base.

Registered under connector_id ``mysql`` via the package entry points.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.dialects.mysql import insert as mysql_insert

from cdk.sql.dialects import SqlDialect
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


class MySQLConnector(GenericSQLConnector):
    """MySQL connector: the CDK SQL base wired to the MySQL dialect."""

    dialect_class = MySQLDialect
