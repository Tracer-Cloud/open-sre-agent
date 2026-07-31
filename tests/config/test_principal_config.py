import pytest

from config.principal import Actor, Principal, PrincipalKind


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


def test_actor_normalizes_id():
    actor = Actor(id="  user_123  ")

    assert actor.id == "user_123"


def test_actor_empty_id_raises():
    with pytest.raises(ValueError):
        Actor(id="   ")


def test_actor_non_string_id_raises():
    with pytest.raises(TypeError):
        Actor(id=123)
