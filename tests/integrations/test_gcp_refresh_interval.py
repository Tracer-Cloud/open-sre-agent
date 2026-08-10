"""Parsing of the GCP ``*_REFRESH_INTERVAL`` variables.

Small surface, but every branch here is a silent failure if it goes wrong: a
misparse does not raise, it just changes how often — or whether — the deployment
notices a new project or cluster, and nothing in the output says which reading
it took.
"""

from __future__ import annotations

import logging

import pytest

from config.constants.gcp import (
    GCP_DEFAULT_REFRESH_INTERVAL_SECONDS,
    GCP_PROJECT_REFRESH_INTERVAL_ENV,
)
from integrations.gcp.refresh import MIN_INTERVAL_SECONDS, NEVER, is_off, refresh_interval

_ENV = GCP_PROJECT_REFRESH_INTERVAL_ENV


@pytest.fixture(autouse=True)
def _unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)


def test_unset_means_refreshing_is_on() -> None:
    """Refreshing is the default. Opting out is the thing that must be spelled."""
    assert refresh_interval(_ENV) == GCP_DEFAULT_REFRESH_INTERVAL_SECONDS
    assert is_off(refresh_interval(_ENV)) is False


@pytest.mark.parametrize("raw", ["0", "off", "no", "false", "never", "OFF", " 0 "])
def test_off_spellings_disable_refreshing(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(_ENV, raw)

    assert is_off(refresh_interval(_ENV)) is True


def test_a_negative_interval_is_off_rather_than_a_negative_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "-1")

    assert refresh_interval(_ENV) is NEVER


def test_a_plain_number_is_taken_as_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "900")

    assert refresh_interval(_ENV) == 900.0


def test_an_interval_below_the_floor_is_raised_to_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The project listing is read while building the params of every GCP tool
    call, so a sub-minute interval puts a Resource Manager round trip in front
    of each one — the exact cost the cache exists to remove."""
    monkeypatch.setenv(_ENV, "5")

    with caplog.at_level(logging.WARNING):
        interval = refresh_interval(_ENV)

    assert interval == MIN_INTERVAL_SECONDS
    assert _ENV in caplog.text


def test_a_typo_falls_back_to_the_default_not_to_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The two failure modes are not symmetric.

    A typo that silently disabled refreshing looks exactly like refreshing
    working, right up until someone asks why a project created last week is
    still invisible. Falling back to the default is wrong out loud instead.
    """
    monkeypatch.setenv(_ENV, "30m")

    with caplog.at_level(logging.WARNING):
        interval = refresh_interval(_ENV)

    assert interval == GCP_DEFAULT_REFRESH_INTERVAL_SECONDS
    assert is_off(interval) is False
    assert _ENV in caplog.text
