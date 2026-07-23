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
- Driver: mysql
- Default port: 3306
- Connection string format: `mysql://${username}:${password}@${host}:${port}/${database}`
- SSH tunnel support: yes

## Post-Auth Steps

None required.

## Caveats

- Using `localhost` as the host connects via Unix socket by default; use `127.0.0.1` for TCP/IP connections.
- SSL mode defaults to `PREFERRED`, which attempts encrypted connections but falls back to unencrypted if the server does not support SSL.
- `ssl_mode` values are MySQL-native and **case-sensitive**, matching MySQL client `--ssl-mode`: `DISABLED` (no TLS), `PREFERRED` (try TLS, fall back), `REQUIRED` (TLS or fail), `VERIFY_CA` (TLS + verify CA), `VERIFY_IDENTITY` (TLS + verify CA + verify hostname).
- The default character set is `utf8mb4`.
- Port must be an integer.
- SSL CA certificate (`ssl_ca`) is required when `ssl_mode` is set to `VERIFY_CA` or `VERIFY_IDENTITY`.
- No API rate limits apply -- this is a direct database connection.
- `TIMESTAMP` columns are stored as UTC internally but returned through the session `time_zone`. Retrieved `TIMESTAMP` instants are only guaranteed correct when the session time zone is UTC. The connector declares `SET time_zone = '+00:00'` via `session_init_sql` for automatic pinning once the CDK calls that hook; until then, ensure the MySQL server's global `time_zone` is `'+00:00'`.
