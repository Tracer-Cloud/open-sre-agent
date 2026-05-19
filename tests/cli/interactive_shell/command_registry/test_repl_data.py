from __future__ import annotations

from app.cli.interactive_shell.command_registry import repl_data


def test_configured_integration_names_reads_catalog_without_verify(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.verify.resolve_effective_integrations",
        lambda: {"aws": {}, "grafana": {}},
    )
    verify_calls: list[str | None] = []
    monkeypatch.setattr(
        "app.integrations.verify.verify_integrations",
        lambda service=None, **_kwargs: verify_calls.append(service) or [],
    )

    assert repl_data.configured_integration_names() == ["aws", "grafana"]
    assert verify_calls == []


def test_verify_integration_checks_one_service(monkeypatch) -> None:
    calls: list[str | None] = []

    def _verify(service: str | None = None, **kwargs: object) -> list[dict[str, str]]:
        calls.append(service)
        return [{"service": service or "", "source": "env", "status": "ok", "detail": "ok"}]

    monkeypatch.setattr("app.integrations.verify.verify_integrations", _verify)

    row = repl_data.verify_integration("aws")

    assert calls == ["aws"]
    assert row is not None
    assert row["service"] == "aws"
