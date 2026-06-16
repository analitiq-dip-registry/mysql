import ssl


def ca_ssl_context(ca_pem: str, check_hostname: bool = False) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = check_hostname
    if not check_hostname:
        ctx.verify_mode = ssl.CERT_NONE
    return ctx
