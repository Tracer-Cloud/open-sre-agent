"""Unit tests for app/utils/sentry_sdk.py."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


def _clear_cached_sentry() -> None:
    """Clear any cached state so tests don't bleed into each other."""
    from app.utils import sentry_sdk

    sentry_sdk._init_sentry_once.cache_clear()  # type: ignore[attr-defined]


def _mock_sentry() -> MagicMock:
    """Inject a MagicMock as the sentry_sdk module so lazy imports resolve to it."""
    mock = MagicMock()
    sys.modules["sentry_sdk"] = mock  # type: ignore[index]
    return mock


# -------------------------------------------------------------------
#  No-DSN safety path — the critical guarantee for local development.
# -------------------------------------------------------------------


class TestInitSentryNoDSN:
    """Tests for the no-DSN (no-op) code path in init_sentry()."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        """Teardown-safe reset: clears cache and removes mock before and after each test."""
        _clear_cached_sentry()
        sys.modules.pop("sentry_sdk", None)
        yield
        _clear_cached_sentry()
        sys.modules.pop("sentry_sdk", None)

    def test_no_op_when_dsn_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """init_sentry() must not raise when SENTRY_DSN is an empty string."""
        monkeypatch.setenv("SENTRY_DSN", "")

        from app.utils.sentry_sdk import init_sentry

        init_sentry()  # must not raise

    def test_no_op_when_dsn_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """init_sentry() must not raise when SENTRY_DSN is entirely absent."""
        monkeypatch.delenv("SENTRY_DSN", raising=False)

        from app.utils.sentry_sdk import init_sentry

        init_sentry()  # must not raise

    def test_no_sdk_init_call_when_dsn_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a DSN, sentry_sdk.init must never be called.

        Mock is installed before the import so any lazy import inside
        init_sentry() resolves to our sentinel mock.
        """
        monkeypatch.delenv("SENTRY_DSN", raising=False)

        _mock_sentry()  # must be installed before the lazy import happens
        from app.utils.sentry_sdk import init_sentry

        init_sentry()
        assert sys.modules["sentry_sdk"].init.call_count == 0


# -------------------------------------------------------------------
#  DSN code path — test _init_sentry_once directly.
#  The function has a lazy 'import sentry_sdk' inside it, so we mock
#  sys.modules["sentry_sdk"] before calling it. This avoids modifying
#  production code entirely.
# -------------------------------------------------------------------


class TestInitSentryOnce:
    """Tests for _init_sentry_once (the cached SDK initialiser)."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        """Teardown-safe reset: clears cache and removes mock before and after each test."""
        _clear_cached_sentry()
        sys.modules.pop("sentry_sdk", None)
        yield
        _clear_cached_sentry()
        sys.modules.pop("sentry_sdk", None)

    def test_calls_sentry_sdk_init(self) -> None:
        """_init_sentry_once must call sentry_sdk.init exactly once."""
        from app.utils.sentry_sdk import _init_sentry_once

        _mock_sentry()
        _init_sentry_once(
            dsn="https://abc123@sentry.io/12345",
            environment="test",
            release="opensre@v1.2.3",
            traces_sample_rate=0.2,
        )
        assert sys.modules["sentry_sdk"].init.call_count == 1

    def test_dsn_passed_to_sdk(self) -> None:
        """The DSN argument must be forwarded to sentry_sdk.init."""
        from app.utils.sentry_sdk import _init_sentry_once

        _mock_sentry()
        _init_sentry_once(
            dsn="https://abc123@sentry.io/12345",
            environment="test",
            release="opensre@v1.2.3",
            traces_sample_rate=0.2,
        )
        assert (
            sys.modules["sentry_sdk"].init.call_args.kwargs["dsn"]
            == "https://abc123@sentry.io/12345"
        )

    def test_environment_passed_to_sdk(self) -> None:
        """The environment argument must be forwarded to sentry_sdk.init."""
        from app.utils.sentry_sdk import _init_sentry_once

        _mock_sentry()
        _init_sentry_once(
            dsn="https://abc123@sentry.io/12345",
            environment="prod",
            release="opensre@v1.2.3",
            traces_sample_rate=0.2,
        )
        assert sys.modules["sentry_sdk"].init.call_args.kwargs["environment"] == "prod"

    def test_release_tag_format(self) -> None:
        """The release tag must follow the 'opensre@<version>' convention."""
        from app.utils.sentry_sdk import _init_sentry_once

        _mock_sentry()
        _init_sentry_once(
            dsn="https://abc123@sentry.io/12345",
            environment="test",
            release="opensre@v1.2.3",
            traces_sample_rate=0.2,
        )
        release: str = sys.modules["sentry_sdk"].init.call_args.kwargs["release"]
        assert release.startswith("opensre@")

    def test_traces_sample_rate_passed_through(self) -> None:
        """traces_sample_rate is forwarded to the SDK."""
        from app.utils.sentry_sdk import _init_sentry_once

        _mock_sentry()
        _init_sentry_once(
            dsn="https://abc123@sentry.io/12345",
            environment="test",
            release="opensre@test",
            traces_sample_rate=0.5,
        )
        assert sys.modules["sentry_sdk"].init.call_args.kwargs["traces_sample_rate"] == 0.5

    def test_send_default_pii_is_true(self) -> None:
        """send_default_pii is hard-coded to True."""
        from app.utils.sentry_sdk import _init_sentry_once

        _mock_sentry()
        _init_sentry_once(
            dsn="https://abc123@sentry.io/12345",
            environment="test",
            release="opensre@v1.2.3",
            traces_sample_rate=0.2,
        )
        assert sys.modules["sentry_sdk"].init.call_args.kwargs["send_default_pii"] is True

    def test_idempotent_calls_only_init_once(self) -> None:
        """Three _init_sentry_once calls with identical args must invoke SDK only once."""
        from app.utils.sentry_sdk import _init_sentry_once

        _mock_sentry()
        _init_sentry_once(
            dsn="https://abc123@sentry.io/12345",
            environment="test",
            release="opensre@test",
            traces_sample_rate=0.2,
        )
        _init_sentry_once(
            dsn="https://abc123@sentry.io/12345",
            environment="test",
            release="opensre@test",
            traces_sample_rate=0.2,
        )
        _init_sentry_once(
            dsn="https://abc123@sentry.io/12345",
            environment="test",
            release="opensre@test",
            traces_sample_rate=0.2,
        )
        assert sys.modules["sentry_sdk"].init.call_count == 1


# -------------------------------------------------------------------
#  Module-level sanity / regression checks.
# -------------------------------------------------------------------


class TestModuleExports:
    """Sanity checks for the module's documented public surface."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        """Teardown-safe reset: clears cache and removes mock before and after each test."""
        _clear_cached_sentry()
        sys.modules.pop("sentry_sdk", None)
        yield
        _clear_cached_sentry()
        sys.modules.pop("sentry_sdk", None)

    def test_exports_init_sentry(self) -> None:
        """The module must export init_sentry."""
        from app.utils import sentry_sdk

        assert hasattr(sentry_sdk, "init_sentry")
        assert callable(sentry_sdk.init_sentry)

    def test_init_sentry_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """init_sentry() must return None (not raise) when DSN is absent."""
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        from app.utils.sentry_sdk import init_sentry

        result = init_sentry()
        assert result is None
