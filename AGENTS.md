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
- MySQL `TIME` columns are read as `Duration` canonicals (unit follows the declared fsp), not time-of-day types. MySQL `TIME` is a signed duration (`-838:59:59` to `+838:59:59`); a time-of-day mapping would corrupt negative or >24 h values.
