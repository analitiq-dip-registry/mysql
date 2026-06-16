class SqlDialect:
    name: str = ""
    quote_char: str = '"'
    system_schemas: tuple = ()
    supports_upsert_sqlalchemy: bool = False

    def current_timestamp_default(self) -> str:
        return "CURRENT_TIMESTAMP"
