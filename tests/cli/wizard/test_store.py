from __future__ import annotations

import json

from app.cli.wizard.store import (
    load_active_remote_name,
    load_local_config,
    load_named_remotes,
    load_remote_ops_config,
    load_remote_url,
    save_local_config,
    save_named_remote,
    save_remote_ops_config,
    save_remote_url,
    set_active_remote,
)


def test_save_local_config_writes_versioned_payload(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    saved_path = save_local_config(
        wizard_mode="quickstart",
        provider="anthropic",
        model="claude-opus-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_MODEL",
        probes={
            "local": {"target": "local", "reachable": True, "detail": "ok"},
            "remote": {"target": "remote", "reachable": False, "detail": "down"},
        },
        path=store_path,
    )

    assert saved_path == store_path

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["wizard"]["mode"] == "quickstart"
    assert payload["wizard"]["configured_target"] == "local"
    assert payload["targets"]["local"]["provider"] == "anthropic"
    assert payload["targets"]["local"]["model"] == "claude-opus-4-5"
    assert "api_key" not in payload["targets"]["local"]
    assert payload["probes"]["remote"]["reachable"] is False


def test_load_local_config_returns_independent_empty_payloads(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    first = load_local_config(store_path)
    first["targets"]["local"] = {"provider": "anthropic"}

    second = load_local_config(store_path)

    assert second["targets"] == {}


def test_remote_ops_config_round_trip(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    save_remote_ops_config(
        provider="railway",
        project="proj-a",
        service="svc-a",
        path=store_path,
    )

    loaded = load_remote_ops_config(store_path)
    assert loaded == {"provider": "railway", "project": "proj-a", "service": "svc-a"}


def test_remote_ops_config_clears_project_and_service(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    save_remote_ops_config(
        provider="railway",
        project="proj-b",
        service="svc-b",
        path=store_path,
    )
    save_remote_ops_config(
        provider="railway",
        project=None,
        service=None,
        path=store_path,
    )

    loaded = load_remote_ops_config(store_path)
    assert loaded == {"provider": "railway", "project": None, "service": None}


def test_remote_loaders_treat_malformed_remote_section_as_empty(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"
    store_path.write_text(json.dumps({"version": 1, "remote": "bad"}) + "\n", encoding="utf-8")

    assert load_remote_url(store_path) is None
    assert load_named_remotes(store_path) == {}
    assert load_active_remote_name(store_path) is None


def test_named_remote_loader_skips_malformed_entries(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"
    store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "remote": {
                    "remotes": {
                        "bad": "https://bad.example",
                        "missing_url": {},
                        "prod": {"url": "https://prod.example"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_named_remotes(store_path) == {"prod": "https://prod.example"}


def test_remote_savers_replace_malformed_remote_section(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"
    store_path.write_text(json.dumps({"version": 1, "remote": "bad"}) + "\n", encoding="utf-8")

    save_remote_url("https://remote.example", path=store_path)
    assert load_remote_url(store_path) == "https://remote.example"

    store_path.write_text(json.dumps({"version": 1, "remote": "bad"}) + "\n", encoding="utf-8")
    save_named_remote("prod", "https://prod.example", set_active=True, path=store_path)
    assert load_named_remotes(store_path) == {"prod": "https://prod.example"}
    assert load_active_remote_name(store_path) == "prod"

    store_path.write_text(json.dumps({"version": 1, "remote": "bad"}) + "\n", encoding="utf-8")
    save_remote_ops_config(provider="railway", project=None, service=None, path=store_path)
    assert load_remote_ops_config(store_path) == {
        "provider": "railway",
        "project": None,
        "service": None,
    }


def test_set_active_remote_handles_malformed_named_remotes(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"
    store_path.write_text(
        json.dumps({"version": 1, "remote": {"remotes": "bad"}}) + "\n",
        encoding="utf-8",
    )

    try:
        set_active_remote("prod", path=store_path)
    except KeyError as exc:
        assert str(exc) == "\"No remote named 'prod'\""
    else:
        raise AssertionError("Expected KeyError for missing remote")
