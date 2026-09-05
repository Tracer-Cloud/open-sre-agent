from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest

from config.runbook_sources import RunbookSourceConfig
from core.domain.runbooks import (
    RunbookCatalog,
    RunbookCatalogEntry,
    RunbookDocument,
    RunbookMatch,
    RunbookReference,
)
from core.tool import AgentToolContext
from tools.system.runbook_guidance_tool import load_runbook_guidance
from tools.system.runbook_guidance_tool._evidence import map_runbook_guidance

runbook_tool_module = import_module("tools.system.runbook_guidance_tool.tool")

_SHA = "a" * 40
_CONFIG = RunbookSourceConfig(
    name="platform",
    provider="github",
    repository="acme/operations",
    ref="main",
    manifest=".opensre/runbooks.yaml",
)


class _FakeRunbookSource:
    provider = "github"

    def __init__(
        self,
        *,
        catalog: RunbookCatalog | None = None,
        accepted_url: str = "",
        document_error: Exception | None = None,
    ) -> None:
        self.catalog = catalog
        self.accepted_url = accepted_url
        self.document_error = document_error
        self.fetched_reference: RunbookReference | None = None

    def verify(self) -> tuple[bool, str]:
        return True, "ok"

    def resolve_reference(self, url: str) -> RunbookReference | None:
        if url != self.accepted_url:
            return None
        return RunbookReference(
            source_name="platform",
            document_id="checkout",
            path="runbooks/checkout.md",
            requested_revision="main",
        )

    def fetch_catalog(self) -> RunbookCatalog:
        if self.catalog is None:
            raise RuntimeError("catalog unavailable")
        return self.catalog

    def fetch_document(self, reference: RunbookReference) -> RunbookDocument:
        if self.document_error is not None:
            raise self.document_error
        self.fetched_reference = reference
        return RunbookDocument(
            reference=reference,
            content="# Checkout latency\n\nInspect the latest deployment.",
            resolved_revision=_SHA,
            source_uri=(
                "https://github.com/acme/operations/blob/"
                f"{_SHA}/runbooks/checkout.md"
            ),
            title="Checkout latency",
        )


def _install_source(
    monkeypatch: pytest.MonkeyPatch,
    source: _FakeRunbookSource,
    *,
    configs: tuple[RunbookSourceConfig, ...] = (_CONFIG,),
) -> None:
    monkeypatch.setattr(runbook_tool_module, "load_runbook_sources", lambda: configs)
    monkeypatch.setattr(
        runbook_tool_module,
        "resolve_runbook_source",
        lambda _config, _integrations: source,
    )


def _context() -> AgentToolContext:
    return AgentToolContext(resolved_integrations={"github": {"connection_verified": True}})


def test_explicit_trusted_url_loads_document_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://github.com/acme/operations/blob/main/runbooks/checkout.md"
    source = _FakeRunbookSource(accepted_url=url)
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(runbook_url=url, context=_context())

    assert result["status"] == "loaded"
    assert result["runbook"]["match_reason"] == "explicit_url"
    assert result["runbook"]["revision"] == _SHA
    assert result["runbook"]["url"].endswith(f"{_SHA}/runbooks/checkout.md")


def test_manifest_match_fetches_document_at_catalog_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = RunbookCatalogEntry(
        document_id="checkout-high-latency",
        title="Checkout latency",
        path="runbooks/checkout.md",
        match=RunbookMatch(
            alertname="CheckoutHighLatency",
            labels=(("severity", "critical"),),
        ),
    )
    source = _FakeRunbookSource(
        catalog=RunbookCatalog(
            source_name="platform",
            entries=(entry,),
            resolved_revision=_SHA,
            source_uri=f"https://github.com/acme/operations/blob/{_SHA}/.opensre/runbooks.yaml",
        )
    )
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(
        alertname="CheckoutHighLatency",
        labels={"severity": "critical", "region": "us-east-1"},
        context=_context(),
    )

    assert result["status"] == "loaded"
    assert result["runbook"]["match_reason"] == "alertname_labels"
    assert result["runbook"]["matched_fields"] == ["alertname", "label:severity"]
    assert source.fetched_reference is not None
    assert source.fetched_reference.requested_revision == _SHA


def test_equal_manifest_matches_are_reported_without_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = tuple(
        RunbookCatalogEntry(
            document_id=document_id,
            path=f"runbooks/{document_id}.md",
            match=RunbookMatch(alertname="CheckoutDown"),
        )
        for document_id in ("checkout-a", "checkout-b")
    )
    source = _FakeRunbookSource(
        catalog=RunbookCatalog(
            source_name="platform",
            entries=entries,
            resolved_revision=_SHA,
            source_uri="manifest",
        )
    )
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(alertname="CheckoutDown", context=_context())

    assert result["status"] == "ambiguous"
    assert result["candidates"] == ["platform/checkout-a", "platform/checkout-b"]
    assert source.fetched_reference is None


def test_untrusted_url_is_rejected_without_fallback_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeRunbookSource()
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(
        runbook_url="https://example.test/runbook.md",
        alertname="CheckoutDown",
        context=_context(),
    )

    assert result["available"] is False
    assert result["status"] == "unavailable"
    assert "accepts this URL" in result["message"]


def test_retrieval_failure_does_not_expose_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://github.com/acme/operations/blob/main/runbooks/checkout.md"
    source = _FakeRunbookSource(
        accepted_url=url,
        document_error=RuntimeError("token ghp_secret rejected by private repository"),
    )
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(runbook_url=url, context=_context())

    assert result["available"] is False
    assert result["status"] == "unavailable"
    assert "ghp_secret" not in str(result)


def test_loaded_runbook_becomes_citeable_evidence() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "status": "loaded",
        "runbook": {
            "title": "Checkout latency",
            "path": "runbooks/checkout.md",
            "revision": _SHA,
            "url": f"https://github.com/acme/operations/blob/{_SHA}/runbooks/checkout.md",
        },
    }

    map_runbook_guidance(evidence, output, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "load_runbook_guidance",
            "label": "Runbook Guidance",
            "summary": f"Checkout latency: runbooks/checkout.md@{_SHA}",
            "url": output["runbook"]["url"],
            "snippet": None,
        }
    ]
