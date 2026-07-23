"""MySQL connector — dialect + connector class for the Analitiq CDK.

Everything MySQL-specific lives here, in the connector package: backtick
identifier quoting, the system-schema list, the ``ON DUPLICATE KEY
UPDATE`` upsert statement, and the ``LOAD DATA LOCAL INFILE`` bulk-write
path for the ``truncate_insert`` write mode.

MySQL runs on the SQLAlchemy + aiomysql transport (no ADBC driver).
The ADBC hooks stay on the neutral base.

Registered under connector_id ``mysql`` via the package entry points.

LOAD DATA LOCAL INFILE bulk path
---------------------------------
For ``truncate_insert`` streams (full-refresh ETL), the connector tries
``LOAD DATA LOCAL INFILE`` before falling back to the batched-INSERT path.
The bulk path requires ``local_infile=True`` at the connection level, which
``MySQLDialect.build_tls_connect_args`` injects unconditionally.  If the
server or client has ``local_infile`` disabled (MySQL errors ER 1148 /
ER 3948), the connector falls back silently and disables the bulk path for
the life of the connection.

The bulk path is skipped for ``insert`` (requires anti-join dedup) and
``upsert`` (handled by ``ON DUPLICATE KEY UPDATE``), and also when any
column value is ``bytes`` (binary data requires per-column UNHEX() and is
deferred to the regular INSERT path).
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import tempfile
from datetime import date, datetime
from typing import Any

import ssl as _ssl

import pymysql.err as _pymysql_err
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from cdk.sql.dialects import SqlDialect
from cdk.transport_factory import ca_ssl_context
from cdk.sql.generic import GenericSQLConnector

logger = logging.getLogger(__name__)

# MySQL error codes that mean local_infile is disabled on the server or client.
_ER_LOCAL_INFILE_DISABLED: frozenset[int] = frozenset({1148, 3948})


class _BinaryColumnError(Exception):
    """Raised when a batch contains bytes values unsupported by the TSV path."""


def _tsv_value(v: Any) -> str:
    """Serialise one record value to a MySQL LOAD DATA INFILE TSV cell.

    Returns ``\\N`` for None, ``1``/``0`` for bool, ISO strings for
    datetime/date, JSON for dict/list, and backslash-escaped text for
    strings.  Raises ``_BinaryColumnError`` for ``bytes`` (binary data
    cannot be safely encoded as TSV and must use the INSERT path instead).
    Raises ``_BinaryColumnError`` for non-finite floats (NaN/±Inf are not
    valid MySQL numeric literals and must use the INSERT path).
    """
    if v is None:
        return r"\N"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise _BinaryColumnError(
                f"non-finite float ({v!r}) cannot be serialized to TSV; "
                "deferring batch to INSERT path"
            )
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, bytes):
        raise _BinaryColumnError(
            "bytes value cannot be serialized to TSV for LOAD DATA LOCAL INFILE"
        )
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        # JSON columns arrive as Python dicts/lists; dump to valid JSON text.
        s = json.dumps(v, default=str)
        return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    s = str(v)
    return (
        s.replace("\\", "\\\\")
         .replace("\t", "\\t")
         .replace("\n", "\\n")
         .replace("\r", "\\r")
    )


class MySQLDialect(SqlDialect):
    """MySQL SQL strategy: a schema is a database; identifiers use backticks."""

    name = "mysql"
    quote_char = "`"
    system_schemas = ("information_schema", "mysql", "performance_schema", "sys")
    supports_upsert_sqlalchemy = True

    def build_sqlalchemy_upsert(
        self,
        table: Any,
        records: list[dict[str, Any]],
        conflict_keys: list[str],
    ) -> Any:
        stmt = mysql_insert(table).values(records)
        record_columns = set(records[0].keys())
        update_cols = {
            c.name: c
            for c in stmt.inserted
            if c.name not in conflict_keys and c.name in record_columns
        }
        return stmt.on_duplicate_key_update(**update_cols)

    def current_timestamp_default(self) -> str:
        # MySQL requires the fractional-seconds precision (fsp) in a DEFAULT
        # expression to match the column's fsp exactly; a mismatch is rejected
        # with error 1067 "Invalid default value". The CDK write-map targets
        # DATETIME(6) for all Timestamp canonical types (definition/type-map-write.json),
        # so the default must be CURRENT_TIMESTAMP(6).
        return "CURRENT_TIMESTAMP(6)"

    def batch_commits_key_type(self, type_mapper) -> str:
        # TEXT (the write map's Utf8) cannot be a primary key on
        # MySQL/MariaDB without a prefix length; the engine's idempotency
        # keys are bounded identifiers, so a bounded VARCHAR is correct.
        return "VARCHAR(255)"

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

    def build_tls_connect_args(self, mode: str, ca_pem: str | None) -> dict[str, Any]:
        # Include local_infile so LOAD DATA LOCAL INFILE is available for
        # bulk writes; the connector falls back on ER 1148/3948 if the
        # server disables it.
        args = super().build_tls_connect_args(mode, ca_pem)
        args["local_infile"] = True
        return args


class MySQLConnector(GenericSQLConnector):
    """MySQL connector: CDK SQL base wired to the MySQL dialect.

    Adds a ``LOAD DATA LOCAL INFILE`` fast path for ``truncate_insert``
    streams; falls back to batched INSERT on ER 1148/3948.
    """

    dialect_class = MySQLDialect

    def __init__(self) -> None:
        super().__init__()
        # Flipped to True on the first ER 1148/3948 response and stays True
        # for the lifetime of this connection so subsequent batches skip
        # straight to the INSERT path.
        self._local_infile_disabled: bool = False

    def _truncate_and_insert(
        self,
        conn: Connection,
        state: Any,
        records: list[dict[str, Any]],
        truncate_now: bool,
    ) -> None:
        """Full-refresh write: optionally truncate then load the batch.

        Attempts ``LOAD DATA LOCAL INFILE`` for bulk throughput.  Falls
        back to the base-class batched-INSERT on ER 1148/3948 (server or
        client has ``local_infile`` disabled) or when any value cannot be
        serialized to TSV (bytes / NaN / Inf).
        """
        if state.table is None:
            return
        if truncate_now:
            conn.execute(state.table.delete())
        if records and not self._local_infile_disabled:
            try:
                self._load_data_local_infile(conn, state.address, records)
                return
            except _BinaryColumnError as exc:
                # Non-serializable value in batch — defer to INSERT.
                # No flag flip: other batches without this value will still
                # use the fast path.
                logger.debug(
                    "LOAD DATA LOCAL INFILE skipped for batch: %s; "
                    "falling back to batched-INSERT",
                    exc,
                )
            except (OperationalError, _pymysql_err.OperationalError) as exc:
                # The raw aiomysql cursor raises pymysql.err.OperationalError
                # directly (bypassing SQLAlchemy's exception-wrapping layer);
                # the SQLAlchemy subclass covers any future path that does go
                # through the SA boundary.
                if isinstance(exc, OperationalError):
                    orig = getattr(exc, "orig", None)
                    mysql_errno = orig.args[0] if orig and orig.args else None
                else:
                    mysql_errno = exc.args[0] if exc.args else None
                if mysql_errno in _ER_LOCAL_INFILE_DISABLED:
                    logger.info(
                        "LOAD DATA LOCAL INFILE disabled (MySQL ER %s); "
                        "falling back to batched-INSERT for this connection",
                        mysql_errno,
                    )
                    self._local_infile_disabled = True
                else:
                    raise
        self._insert_records(conn, state, records)

    def _load_data_local_infile(
        self,
        conn: Connection,
        address: Any,
        records: list[dict[str, Any]],
    ) -> None:
        """Stream *records* into *address* via ``LOAD DATA LOCAL INFILE``.

        Serialises the batch as tab-separated values into a secure temp
        file, then executes the statement through the raw aiomysql cursor
        so aiomysql can handle the LOCAL file-transfer protocol.  The temp
        file is deleted whether or not the statement succeeds.

        Raises ``_BinaryColumnError`` if any value cannot be encoded as
        TSV (bytes, NaN, Inf).  Raises ``pymysql.err.OperationalError``
        on MySQL errors, including ER 1148/3948 for disabled
        ``local_infile``.
        """
        if not records:
            return

        columns = list(records[0].keys())
        quoted_table = self.dialect.quote_table(address)
        quoted_cols = ", ".join(self.dialect.quote_ident(c) for c in columns)

        # Serialise to TSV; raises _BinaryColumnError on unsupported values.
        buf = io.StringIO()
        for record in records:
            buf.write("\t".join(_tsv_value(record[c]) for c in columns))
            buf.write("\n")

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".tsv",
            encoding="utf-8",
            delete=False,
        ) as tmp:
            tmp.write(buf.getvalue())
            tmp_path = tmp.name

        try:
            # Escape the path for a single-quoted SQL string literal.
            escaped = tmp_path.replace("\\", "\\\\").replace("'", "\\'")
            sql = (
                f"LOAD DATA LOCAL INFILE '{escaped}' "  # nosec B608
                f"REPLACE INTO TABLE {quoted_table} "
                "CHARACTER SET utf8mb4 "
                "FIELDS TERMINATED BY '\\t' ESCAPED BY '\\\\' "
                "LINES TERMINATED BY '\\n' "
                f"({quoted_cols})"
            )
            # Access the underlying aiomysql cursor through SQLAlchemy's
            # sync-adapter shim.  Within run_sync, the adapter wraps each
            # async aiomysql call with asyncio.run_coroutine_threadsafe so
            # this execute() call is synchronous from our perspective.
            raw_cursor = conn.connection.dbapi_connection.cursor()
            try:
                raw_cursor.execute(sql)
            finally:
                raw_cursor.close()
        finally:
            os.unlink(tmp_path)
