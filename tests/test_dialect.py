import ssl

import pytest

from connector import MySQLDialect


@pytest.fixture
def dialect():
    return MySQLDialect()


class TestBuildTlsConnectArg:
    def test_disabled_returns_false(self, dialect):
        assert dialect.build_tls_connect_arg("DISABLED", None) is False

    def test_preferred_returns_ssl_context_with_cert_none(self, dialect):
        ctx = dialect.build_tls_connect_arg("PREFERRED", None)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_NONE
        assert not ctx.check_hostname

    def test_required_returns_ssl_context_with_cert_none(self, dialect):
        ctx = dialect.build_tls_connect_arg("REQUIRED", None)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_NONE
        assert not ctx.check_hostname

    def test_verify_ca_without_ca_pem_raises(self, dialect):
        with pytest.raises(ValueError, match="VERIFY_CA"):
            dialect.build_tls_connect_arg("VERIFY_CA", None)

    def test_verify_identity_without_ca_pem_raises(self, dialect):
        with pytest.raises(ValueError, match="VERIFY_IDENTITY"):
            dialect.build_tls_connect_arg("VERIFY_IDENTITY", None)

    def test_unknown_mode_raises(self, dialect):
        with pytest.raises(ValueError, match="not recognized"):
            dialect.build_tls_connect_arg("INVALID_MODE", None)

    def test_mode_matching_is_case_insensitive(self, dialect):
        assert dialect.build_tls_connect_arg("disabled", None) is False


class TestCurrentTimestampDefault:
    def test_returns_current_timestamp_with_microsecond_precision(self, dialect):
        assert dialect.current_timestamp_default() == "CURRENT_TIMESTAMP(6)"
