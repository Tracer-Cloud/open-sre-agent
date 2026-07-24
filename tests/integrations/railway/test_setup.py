from __future__ import annotations

from integrations.railway.setup import RAILWAY_SETUP
from integrations.railway.verifier import verify_railway


def test_verify_railway_requires_a_token_or_a_complete_default_scope() -> None:
    outcome = verify_railway("setup", {})

    assert outcome["status"] == "missing"
    assert outcome["detail"].startswith("Provide a Railway token")


def test_verify_railway_accepts_a_complete_default_scope_without_a_token() -> None:
    # A full project/service/environment satisfies the cross-field rule, so the
    # verifier moves past it to the probe rather than failing as "missing" here.
    outcome = verify_railway(
        "setup",
        {"project": "project-id", "service": "service-id", "environment": "production"},
    )

    assert not outcome["detail"].startswith("Provide a Railway token")


def test_railway_setup_spec_is_wired_through_the_shared_flow() -> None:
    assert RAILWAY_SETUP.service == "railway"
    assert RAILWAY_SETUP.verify is verify_railway
    fields = {field.name: field for field in RAILWAY_SETUP.fields}
    # The either/or rule lives in the verifier, so none of the scope-identity
    # fields is individually required at collection time.
    assert fields["token"].required is False
    assert fields["project"].required is False
    assert fields["service"].required is False
    # Defaults keep enter-through parity with a non-prompting surface.
    assert fields["environment"].default == "production"
    assert fields["railway_path"].default == "railway"
