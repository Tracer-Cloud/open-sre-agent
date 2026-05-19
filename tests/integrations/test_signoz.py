"""Unit tests for the SigNoz integration module."""

from app.integrations.catalog import load_env_integrations
from app.integrations.signoz import (
    SigNozConfig,
    SigNozValidationResult,
    build_signoz_config,
    signoz_config_from_env,
    signoz_extract_params,
    signoz_is_available,
)


class TestSigNozConfig:
    """Tests for SigNozConfig model."""

    def test_defaults(self) -> None:
        config = SigNozConfig(clickhouse_host="localhost")
        assert config.clickhouse_host == "localhost"
        assert config.clickhouse_port == 8123
        assert config.clickhouse_database == "default"
        assert config.clickhouse_user == "default"
        assert config.clickhouse_password == ""
        assert config.secure is False
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50
        assert config.url == ""
        assert config.api_key == ""

    def test_is_configured_with_host(self) -> None:
        config = SigNozConfig(clickhouse_host="ch.example.com")
        assert config.is_configured is True

    def test_is_configured_without_host(self) -> None:
        config = SigNozConfig()
        assert config.is_configured is False

    def test_normalize_host_strips_whitespace(self) -> None:
        config = SigNozConfig(clickhouse_host="  ch.example.com  ")
        assert config.clickhouse_host == "ch.example.com"

    def test_normalize_database_default(self) -> None:
        config = SigNozConfig(clickhouse_host="localhost", clickhouse_database="")
        assert config.clickhouse_database == "default"

    def test_normalize_user_default(self) -> None:
        config = SigNozConfig(clickhouse_host="localhost", clickhouse_user="")
        assert config.clickhouse_user == "default"

    def test_to_clickhouse_config(self) -> None:
        config = SigNozConfig(
            clickhouse_host="ch.prod.internal",
            clickhouse_port=9440,
            clickhouse_database="analytics",
            clickhouse_user="reader",
            clickhouse_password="secret",
            secure=True,
            timeout_seconds=30.0,
            max_results=100,
        )
        ch = config.to_clickhouse_config()
        assert ch.host == "ch.prod.internal"
        assert ch.port == 9440
        assert ch.database == "analytics"
        assert ch.username == "reader"
        assert ch.password == "secret"
        assert ch.secure is True
        assert ch.timeout_seconds == 30.0
        assert ch.max_results == 100


class TestBuildSigNozConfig:
    """Tests for build_signoz_config helper."""

    def test_from_dict(self) -> None:
        config = build_signoz_config({"clickhouse_host": "ch.example.com", "clickhouse_port": 8123})
        assert config.clickhouse_host == "ch.example.com"
        assert config.clickhouse_port == 8123

    def test_from_none(self) -> None:
        config = build_signoz_config(None)
        assert config.clickhouse_host == ""
        assert config.is_configured is False

    def test_from_empty_dict(self) -> None:
        config = build_signoz_config({})
        assert config.clickhouse_host == ""
        assert config.is_configured is False


class TestSigNozConfigFromEnv:
    """Tests for signoz_config_from_env helper."""

    def test_returns_none_without_host(self) -> None:
        import os

        old = os.environ.get("SIGNOZ_CLICKHOUSE_HOST")
        os.environ.pop("SIGNOZ_CLICKHOUSE_HOST", None)
        try:
            result = signoz_config_from_env()
            assert result is None
        finally:
            if old is not None:
                os.environ["SIGNOZ_CLICKHOUSE_HOST"] = old

    def test_returns_config_with_host(self) -> None:
        import os

        os.environ["SIGNOZ_CLICKHOUSE_HOST"] = "ch.test.local"
        os.environ["SIGNOZ_CLICKHOUSE_PORT"] = "9440"
        os.environ["SIGNOZ_CLICKHOUSE_DATABASE"] = "testdb"
        os.environ["SIGNOZ_CLICKHOUSE_USER"] = "testuser"
        os.environ["SIGNOZ_CLICKHOUSE_PASSWORD"] = "testpass"
        os.environ["SIGNOZ_CLICKHOUSE_SECURE"] = "true"
        os.environ["SIGNOZ_URL"] = "http://localhost:3301"
        os.environ["SIGNOZ_API_KEY"] = "sk-test"
        try:
            config = signoz_config_from_env()
            assert config is not None
            assert config.clickhouse_host == "ch.test.local"
            assert config.clickhouse_port == 9440
            assert config.clickhouse_database == "testdb"
            assert config.clickhouse_user == "testuser"
            assert config.clickhouse_password == "testpass"
            assert config.secure is True
            assert config.url == "http://localhost:3301"
            assert config.api_key == "sk-test"
        finally:
            for key in [
                "SIGNOZ_CLICKHOUSE_HOST",
                "SIGNOZ_CLICKHOUSE_PORT",
                "SIGNOZ_CLICKHOUSE_DATABASE",
                "SIGNOZ_CLICKHOUSE_USER",
                "SIGNOZ_CLICKHOUSE_PASSWORD",
                "SIGNOZ_CLICKHOUSE_SECURE",
                "SIGNOZ_URL",
                "SIGNOZ_API_KEY",
            ]:
                os.environ.pop(key, None)


class TestSigNozValidationResult:
    """Tests for SigNozValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = SigNozValidationResult(ok=True, detail="Connected.")
        assert result.ok is True
        assert result.detail == "Connected."

    def test_error_result(self) -> None:
        result = SigNozValidationResult(ok=False, detail="Connection refused.")
        assert result.ok is False
        assert result.detail == "Connection refused."


class TestSigNozIsAvailable:
    """Tests for signoz_is_available helper."""

    def test_available_when_connection_verified(self) -> None:
        sources = {"signoz": {"connection_verified": True}}
        assert signoz_is_available(sources) is True

    def test_unavailable_without_connection_verified(self) -> None:
        sources = {"signoz": {"clickhouse_host": "localhost"}}
        assert signoz_is_available(sources) is False

    def test_unavailable_when_missing(self) -> None:
        sources = {}
        assert signoz_is_available(sources) is False


class TestSigNozExtractParams:
    """Tests for signoz_extract_params helper."""

    def test_extracts_params(self) -> None:
        sources = {
            "signoz": {
                "clickhouse_host": "ch.example.com",
                "clickhouse_port": 8123,
                "clickhouse_database": "default",
                "clickhouse_user": "default",
                "clickhouse_password": "secret",
                "secure": True,
                "url": "http://signoz.example.com",
                "api_key": "key",
            }
        }
        params = signoz_extract_params(sources)
        assert params["clickhouse_host"] == "ch.example.com"
        assert params["clickhouse_port"] == 8123
        assert params["clickhouse_database"] == "default"
        assert params["clickhouse_user"] == "default"
        assert params["clickhouse_password"] == "secret"
        assert params["secure"] is True
        assert params["url"] == "http://signoz.example.com"
        assert params["api_key"] == "key"

    def test_uses_defaults_when_missing(self) -> None:
        sources = {}
        params = signoz_extract_params(sources)
        assert params["clickhouse_host"] == ""
        assert params["clickhouse_port"] == 8123
        assert params["clickhouse_database"] == "default"
        assert params["clickhouse_user"] == "default"
        assert params["clickhouse_password"] == ""
        assert params["secure"] is False
        assert params["url"] == ""
        assert params["api_key"] == ""


class TestSigNozEnvCatalogLoading:
    """Tests for SigNoz env loading in load_env_integrations."""

    def test_invalid_port_does_not_raise(self, monkeypatch) -> None:
        monkeypatch.setenv("SIGNOZ_CLICKHOUSE_HOST", "localhost")
        monkeypatch.setenv("SIGNOZ_CLICKHOUSE_PORT", "abc")
        records = load_env_integrations()
        assert isinstance(records, list)
        assert all(record.get("service") != "signoz" for record in records)
