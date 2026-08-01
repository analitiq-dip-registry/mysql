---
name: MySQL
description: >
  Open-source relational database management system for structured data storage, querying, and management
type: database
---

# MySQL

Open-source relational database management system (RDBMS) for structured data storage, querying, and management. MySQL is one of the most widely used databases, powering web applications, data warehouses, and transactional systems.

## Authentication

### Database Credentials (username/password)
- Driver: mysql+aiomysql (async SQLAlchemy transport)
- Default port: 3306
- Connection string format: `mysql+aiomysql://${username}:${password}@${host}:${port}/${database}`
- SSH tunnel support: yes

## Post-Auth Steps

None required.

## Caveats

- Prefer `127.0.0.1` over `localhost` to avoid DNS/IPv6 resolution ambiguity (the aiomysql driver always connects over TCP/IP; Unix-socket special-casing of `localhost` applies to other MySQL clients).
- SSL mode defaults to `PREFERRED`, which attempts encrypted connections but falls back to unencrypted if the server does not support SSL.
- `ssl_mode` values follow the MySQL client `--ssl-mode` vocabulary: `DISABLED` (no TLS), `PREFERRED` (try TLS, fall back), `REQUIRED` (TLS without certificate verification), `VERIFY_CA` (TLS + verify CA), `VERIFY_IDENTITY` (TLS + verify CA + verify hostname). The driver negotiates TLS only when the server advertises support; the connector therefore verifies post-connect (`Ssl_cipher` probe) that `REQUIRED`/`VERIFY_*` sessions are encrypted and fails the connection otherwise.
- The default character set is `utf8mb4`.
- Port must be an integer.
- SSL CA certificate (`ssl_ca_certificate`) is required when `ssl_mode` is set to `VERIFY_CA` or `VERIFY_IDENTITY`.
- No API rate limits apply -- this is a direct database connection.
- MySQL `TIME` columns are read as `Duration` canonicals (unit follows the declared fsp), not time-of-day types. MySQL `TIME` is a signed duration (`-838:59:59` to `+838:59:59`); a time-of-day mapping would corrupt negative or >24 h values.
- MySQL stores `TIMESTAMP` values as UTC but returns them converted through the session `time_zone`. The connector pins every new connection to UTC (`SET time_zone = '+00:00'` via the CDK's `session_init_sql` hook), so retrieved instants are correct regardless of the server's global setting. `DATETIME` stored values are zoneless and unaffected, but `CURRENT_TIMESTAMP`/`NOW()` defaults evaluated on connector connections now generate UTC wall-clock values (consistent with the canonicals).
- Write-path text columns are capped: the `Utf8` canonical renders `VARCHAR(255)`. MySQL rejects `TEXT`/`BLOB` in a key without a prefix length and the engine declares its keyless-stream dedup column as a `Utf8` primary key, so one rendering serves both roles. Values over 255 characters fail loudly (error 1406 under `STRICT_TRANS_TABLES`); more than ~64 string columns can exceed the 65,535-byte row limit (error 1118). `LargeUtf8` renders `LONGTEXT` for genuinely long text.
- Bulk load is not used. `LOAD DATA LOCAL INFILE` needs the client connection opened with `local_infile=True`, which aiomysql defaults to off and the engine's SQLAlchemy transport exposes no channel to set; batches land via `executemany`.
- Resource discovery excludes the `information_schema`, `mysql`, `performance_schema` and `sys` schemas.
- The merge form is `INSERT ... SELECT ... ON DUPLICATE KEY UPDATE` using `VALUES(col)`. `VALUES()` is deprecated as of MySQL 8.0.20 but remains functional in 8.4 LTS; the 8.0.19 row-alias replacement is not available on an `INSERT ... SELECT`.
