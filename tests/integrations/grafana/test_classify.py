from __future__ import annotations

from integrations.grafana import classify


def test_classify_accepts_read_token_alias() -> None:
    cfg, source = classify(
        {
            "endpoint": "https://grafana.example.com",
            "read_token": "sa-token",
        },
        "rec-1",
    )
    assert source == "grafana"
    assert cfg is not None
    assert cfg.api_key == "sa-token"


def test_classify_prefers_api_key_over_aliases() -> None:
    cfg, source = classify(
        {
            "endpoint": "https://grafana.example.com",
            "api_key": "primary",
            "token": "secondary",
        },
        "rec-2",
    )
    assert source == "grafana"
    assert cfg is not None
    assert cfg.api_key == "primary"
