import pytest

from config.principal import Principal, PrincipalKind


def test_principal_org_creates_org_principal():
    principal = Principal.org("org_123")

    assert principal.kind is PrincipalKind.ORG
    assert isinstance(principal.kind, PrincipalKind)
    assert principal.id == "org_123"


def test_principal_converts_string_kind_to_enum():
    principal = Principal(kind="org", id="org_123")

    assert principal.kind is PrincipalKind.ORG
    assert isinstance(principal.kind, PrincipalKind)


def test_principal_invalid_kind_raises():
    with pytest.raises(ValueError):
        Principal(kind="company", id="org_123")
