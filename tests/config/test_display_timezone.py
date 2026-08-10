"""Times are quoted in the zone the deployment configures, not the pod's.

A gateway pod's timezone is an accident of where it runs and has nothing to do
with where its readers sit, so a team spread across zones gets an answer
somebody has to convert before it can be correlated with an alert.

The three failure modes worth pinning are distinct: the configured zone must
reach *both* the name and the offset (a name-only change reads correct while the
clock stays wrong), unset must stay byte-identical to the previous behaviour, and
an unusable value must not take the process down at boot.
"""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import pytest

from config.constants.timezone import DISPLAY_TIMEZONE_ENV
from config.runtime_metadata.assembly import build_runtime_metadata, capture_runtime_facts
from config.runtime_metadata.probes import local_tz_name

# Deliberately somewhere nobody's CI runs, with a quarter-hour offset no host
# zone will match by accident.
_ELSEWHERE = "Pacific/Chatham"


def test_a_configured_zone_sets_both_the_name_and_the_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DISPLAY_TIMEZONE_ENV, _ELSEWHERE)

    facts = capture_runtime_facts(metadata=build_runtime_metadata())
    stamped = _dt.datetime.fromisoformat(facts["now_iso"])

    assert facts["tz_name"] == _ELSEWHERE
    # Compare offsets rather than a literal: Chatham observes DST, so the
    # correct answer is +12:45 for half the year and +13:45 for the other half.
    assert stamped.utcoffset() == ZoneInfo(_ELSEWHERE).utcoffset(stamped)


def test_leaving_it_unset_still_reports_the_host_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The knob is purely additive — unset must not relocate a laptop's clock."""
    monkeypatch.delenv(DISPLAY_TIMEZONE_ENV, raising=False)

    facts = capture_runtime_facts(metadata=build_runtime_metadata())
    stamped = _dt.datetime.fromisoformat(facts["now_iso"])

    assert facts["tz_name"] == local_tz_name()
    assert stamped.utcoffset() == _dt.datetime.now().astimezone().utcoffset()


def test_an_unusable_zone_falls_back_instead_of_failing_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in a Helm value must not stop the gateway from starting.

    Quoting the wrong zone is a far better outcome than a pod that will not
    come up, and this is resolved on the path that builds every prompt.
    """
    monkeypatch.setenv(DISPLAY_TIMEZONE_ENV, "Middle/Earth")

    facts = capture_runtime_facts(metadata=build_runtime_metadata())

    assert facts["tz_name"] == local_tz_name()
    assert facts["now_iso"]
