import ssl


def ca_ssl_context(ca_pem: str, check_hostname: bool = False) -> ssl.SSLContext:
    # create_default_context() starts with CERT_REQUIRED; mirror real behavior by
    # leaving verify_mode intact and only toggling hostname checking.
    ctx = ssl.create_default_context()
    if not check_hostname:
        ctx.check_hostname = False
    return ctx
