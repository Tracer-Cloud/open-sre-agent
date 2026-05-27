from __future__ import annotations

import pytest

from app.utils.url_validation import is_loopback_host, validate_https_or_loopback_http_url


class TestIsLoopbackHost:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "LOCALHOST",
            "Localhost",
            "127.0.0.1",
            "::1",
            "[::1]",
            "  localhost  ",  # leading/trailing whitespace
        ],
    )
    def test_returns_true_for_loopback(self, host: str) -> None:
        assert is_loopback_host(host) is True

    @pytest.mark.parametrize(
        "host",
        [
            "example.com",
            "192.168.1.1",
            "10.0.0.1",
            "8.8.8.8",
            "not-an-ip",
            "",
            "localhost.evil.com",
        ],
    )
    def test_returns_false_for_non_loopback(self, host: str) -> None:
        assert is_loopback_host(host) is False


class TestValidateHttpsOrLoopbackHttpUrl:
    def test_returns_empty_string_for_empty_input(self) -> None:
        result = validate_https_or_loopback_http_url("", service_name="test")
        assert result == ""

    def test_rejects_whitespace_only_input(self) -> None:
        with pytest.raises(ValueError):
            validate_https_or_loopback_http_url("   ", service_name="test")

    @pytest.mark.parametrize(
        "value",
        [
            "https://example.com",
            "https://api.example.com/v1",
            "https://example.com:8443/path",
            "HTTPS://example.com",  # case-insensitive scheme
        ],
    )
    def test_accepts_https_urls(self, value: str) -> None:
        result = validate_https_or_loopback_http_url(value, service_name="test")
        assert result == value

    @pytest.mark.parametrize(
        "value",
        [
            "http://localhost",
            "http://localhost:8080",
            "http://127.0.0.1",
            "http://127.0.0.1:9090/api",
            "http://[::1]:8080",
        ],
    )
    def test_accepts_http_for_loopback(self, value: str) -> None:
        result = validate_https_or_loopback_http_url(value, service_name="test")
        assert result == value

    @pytest.mark.parametrize(
        "value",
        [
            "http://example.com",
            "http://192.168.1.1",
            "http://10.0.0.1:8080",
        ],
    )
    def test_rejects_http_for_non_loopback(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_https_or_loopback_http_url(value, service_name="MySvc")

    @pytest.mark.parametrize(
        "value",
        [
            "ftp://example.com",
            "ws://example.com",
            "https://",  # missing netloc
        ],
    )
    def test_rejects_invalid_schemes_and_urls(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_https_or_loopback_http_url(value, service_name="MySvc")

    def test_error_message_includes_service_name(self) -> None:
        with pytest.raises(ValueError, match="MySvc"):
            validate_https_or_loopback_http_url("http://example.com", service_name="MySvc")

    def test_custom_field_name(self) -> None:
        result = validate_https_or_loopback_http_url(
            "https://example.com", service_name="test", field_name="endpoint"
        )
        assert result == "https://example.com"
