from __future__ import annotations

import logging
from collections.abc import Generator
from types import ModuleType
from typing import Any

import pytest

from app.tools import registry as registry_module
from app.tools.base import BaseTool
from app.tools.investigation_registry.actions import get_available_actions
from app.tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool
from app.tools.tool_decorator import tool
from app.types.retrieval import RetrievalControls


@pytest.fixture(autouse=True)
def _reset_registry_cache() -> Generator[None]:
    registry_module.clear_tool_registry_cache()
    yield
    registry_module.clear_tool_registry_cache()


def test_tool_decorator_registers_function_tool_with_inferred_schema() -> None:
    module: Any = ModuleType("app.tools.fake_function_tool")

    @tool(
        name="lookup_incident",
        description="Lookup incident metadata.",
        display_name="Incident metadata",
        source="knowledge",
        surfaces=("investigation", "chat"),
    )
    def lookup_incident(incident_id: str, limit: int = 10) -> dict[str, object]:
        return {"incident_id": incident_id, "limit": limit}

    lookup_incident.__module__ = module.__name__
    module.lookup_incident = lookup_incident

    tools = registry_module._collect_registered_tools_from_module(module)

    assert [tool_def.name for tool_def in tools] == ["lookup_incident"]
    registered = tools[0]
    assert registered.input_schema["properties"]["incident_id"]["type"] == "string"
    assert registered.input_schema["properties"]["limit"]["type"] == "integer"
    assert registered.display_name == "Incident metadata"
    assert registered.input_schema["required"] == ["incident_id"]
    assert registered.surfaces == ("investigation", "chat")


def test_tool_decorator_supports_minimal_single_file_function_tool() -> None:
    module: Any = ModuleType("app.tools.single_file_status_tool")

    @tool(source="knowledge")
    def check_status(run_id: str, include_history: bool = False) -> dict[str, object]:
        """Check status for a run."""
        return {"run_id": run_id, "include_history": include_history}

    check_status.__module__ = module.__name__
    module.check_status = check_status

    tools = registry_module._collect_registered_tools_from_module(module)

    assert [tool_def.name for tool_def in tools] == ["check_status"]
    registered = tools[0]
    assert registered.description == "Check status for a run."
    assert registered.source == "knowledge"
    assert registered.input_schema["properties"]["run_id"]["type"] == "string"
    assert registered.input_schema["properties"]["include_history"]["type"] == "boolean"
    assert registered.input_schema["required"] == ["run_id"]
    assert registered.surfaces == ("investigation",)
    assert registered.run(run_id="r-1", include_history=True) == {
        "run_id": "r-1",
        "include_history": True,
    }


def test_function_and_class_tools_share_the_same_runtime_contract() -> None:
    def _available(sources: dict[str, dict[str, str]]) -> bool:
        return bool(sources.get("knowledge"))

    def _extract(sources: dict[str, dict[str, str]]) -> dict[str, str]:
        return {"incident_id": sources["knowledge"]["incident_id"]}

    @tool(
        name="lookup_incident_function",
        description="Lookup incident metadata.",
        source="knowledge",
        input_schema={
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident identifier",
                },
            },
            "required": ["incident_id"],
        },
        surfaces=("investigation", "chat"),
        is_available=_available,
        extract_params=_extract,
        outputs={"incident_id": "Incident identifier"},
    )
    def lookup_incident_function(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    class LookupIncidentClassTool(BaseTool):
        name = "lookup_incident_class"
        description = "Lookup incident metadata."
        source = "knowledge"
        surfaces = ("investigation", "chat")
        input_schema = {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident identifier",
                },
            },
            "required": ["incident_id"],
        }
        outputs = {"incident_id": "Incident identifier"}

        def is_available(self, sources: dict[str, dict[str, str]]) -> bool:
            return _available(sources)

        def extract_params(self, sources: dict[str, dict[str, str]]) -> dict[str, str]:
            return _extract(sources)

        def run(self, incident_id: str) -> dict[str, str]:
            return {"incident_id": incident_id}

    function_tool = getattr(lookup_incident_function, REGISTERED_TOOL_ATTR)
    assert isinstance(function_tool, RegisteredTool)

    class_tool = RegisteredTool.from_base_tool(LookupIncidentClassTool())
    sources = {"knowledge": {"incident_id": "inc-123"}}

    assert function_tool.inputs == class_tool.inputs
    assert function_tool.extract_params(sources) == class_tool.extract_params(sources)
    assert function_tool.is_available(sources) is class_tool.is_available(sources)
    assert function_tool.run(**function_tool.extract_params(sources)) == class_tool.run(
        **class_tool.extract_params(sources)
    )
    assert function_tool.surfaces == class_tool.surfaces


def test_tool_decorator_allows_retrieval_controls_override_for_base_tool() -> None:
    class LookupIncidentClassTool(BaseTool):
        name = "lookup_incident_class"
        description = "Lookup incident metadata."
        source = "knowledge"
        surfaces = ("investigation", "chat")
        retrieval_controls = RetrievalControls(limit=True)
        input_schema = {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident identifier",
                },
            },
            "required": ["incident_id"],
        }

        def run(self, incident_id: str) -> dict[str, str]:
            return {"incident_id": incident_id}

    class_tool = tool(
        LookupIncidentClassTool(),
        retrieval_controls=RetrievalControls(time_bounds=True, filters=True),
    )
    registered = getattr(class_tool, REGISTERED_TOOL_ATTR)
    assert isinstance(registered, RegisteredTool)
    assert registered.retrieval_controls.time_bounds
    assert registered.retrieval_controls.filters
    assert not registered.retrieval_controls.limit


def test_tool_decorator_preserves_tags_and_cost_tier_for_base_tool_instances() -> None:
    class LookupIncidentClassTool(BaseTool):
        name = "lookup_incident_class"
        description = "Lookup incident metadata."
        source = "knowledge"
        input_schema = {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident identifier",
                },
            },
            "required": ["incident_id"],
        }

        def run(self, incident_id: str) -> dict[str, str]:
            return {"incident_id": incident_id}

    decorated = tool(
        LookupIncidentClassTool(),
        tags=("safe", "fast"),
        cost_tier="cheap",
    )

    registered = getattr(decorated, REGISTERED_TOOL_ATTR)
    assert isinstance(registered, RegisteredTool)
    assert registered.tags == ("safe", "fast")
    assert registered.cost_tier == "cheap"


def test_registered_tool_rejects_unknown_cost_tier() -> None:
    def lookup_incident(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    with pytest.raises(ValueError, match="Unsupported cost tier"):
        RegisteredTool.from_function(
            lookup_incident,
            source="knowledge",
            cost_tier="free",  # type: ignore[arg-type]
        )


def test_auto_discovery_populates_investigation_and_chat_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module: Any = ModuleType("app.tools.fake_discovered_tool")

    @tool(
        name="get_incident_metadata",
        description="Return normalized incident metadata.",
        source="knowledge",
        surfaces=("investigation", "chat"),
    )
    def get_incident_metadata(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    get_incident_metadata.__module__ = module.__name__
    module.get_incident_metadata = get_incident_metadata

    monkeypatch.setattr(
        registry_module, "_iter_tool_module_names", lambda: ["fake_discovered_tool"]
    )
    monkeypatch.setattr(registry_module, "_import_tool_module", lambda _name: module)

    assert [
        tool_def.name for tool_def in registry_module.get_registered_tools("investigation")
    ] == ["get_incident_metadata"]
    assert [tool_def.name for tool_def in registry_module.get_registered_tools("chat")] == [
        "get_incident_metadata"
    ]
    assert registry_module.get_registered_tool_map("chat")["get_incident_metadata"].run(
        "inc-1"
    ) == {"incident_id": "inc-1"}


def test_resolve_tool_display_name_prefers_registered_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module: Any = ModuleType("app.tools.fake_display_name_tool")

    @tool(
        name="get_incident_metadata",
        display_name="Incident metadata",
        description="Return normalized incident metadata.",
        source="knowledge",
    )
    def get_incident_metadata(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    get_incident_metadata.__module__ = module.__name__
    module.get_incident_metadata = get_incident_metadata

    monkeypatch.setattr(
        registry_module, "_iter_tool_module_names", lambda: ["fake_display_name_tool"]
    )
    monkeypatch.setattr(registry_module, "_import_tool_module", lambda _name: module)

    assert registry_module.resolve_tool_display_name("get_incident_metadata") == "Incident metadata"


def test_resolve_tool_display_name_falls_back_for_unknown_tools() -> None:
    assert (
        registry_module.resolve_tool_display_name("nonexistent_tool_xyz_sentinel")
        == "nonexistent tool xyz sentinel"
    )


def test_real_registry_discovers_migrated_sre_guidance_tool() -> None:
    action_names = {tool_def.name for tool_def in get_available_actions()}
    assert "get_sre_guidance" in action_names


def test_real_registry_discovers_honeycomb_and_coralogix_tools() -> None:
    action_names = {tool_def.name for tool_def in get_available_actions()}
    assert {"query_honeycomb_traces", "query_coralogix_logs"} <= action_names


def test_real_registry_preserves_existing_chat_tool_surface() -> None:
    chat_names = {tool_def.name for tool_def in registry_module.get_registered_tools("chat")}
    assert {"fetch_failed_run", "get_tracer_run", "search_github_code"} <= chat_names


def test_registry_regression_duplicate_tool_names_across_modules(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that when two modules export the same tool name, only the first is kept."""
    module1: Any = ModuleType("app.tools.first_module")
    module2: Any = ModuleType("app.tools.second_module")

    first_tool = tool(
        name="shared_tool_name",
        description="Tool in first module.",
        source="knowledge",
    )(lambda: {"module": "first"})

    second_tool = tool(
        name="shared_tool_name",
        description="Tool in second module.",
        source="knowledge",
    )(lambda: {"module": "second"})

    first_tool.__module__ = module1.__name__
    second_tool.__module__ = module2.__name__
    module1.shared_tool_first = first_tool
    module2.shared_tool_second = second_tool

    monkeypatch.setattr(
        registry_module,
        "_iter_tool_module_names",
        lambda: ["first_module", "second_module"],
    )
    monkeypatch.setattr(
        registry_module,
        "_import_tool_module",
        lambda name: module1 if name == "first_module" else module2,
    )

    with caplog.at_level(logging.WARNING, logger="app.tools.registry"):
        tools = registry_module.get_registered_tools()

    tool_names = [t.name for t in tools]

    assert tool_names.count("shared_tool_name") == 1
    registered_tool = registry_module.get_registered_tool_map()["shared_tool_name"]
    assert registered_tool.run() == {"module": "first"}

    assert any(
        "Duplicate tool name 'shared_tool_name' across modules" in record.message
        for record in caplog.records
        if record.levelname == "WARNING"
    )


def test_registry_regression_import_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that registry gracefully skips modules with import failures."""
    module: Any = ModuleType("app.tools.valid_tool")

    @tool(
        name="valid_tool",
        description="A valid tool.",
        source="knowledge",
    )
    def valid_tool() -> dict[str, str]:
        return {"status": "ok"}

    valid_tool.__module__ = module.__name__
    module.valid_tool = valid_tool

    def mock_import(name: str) -> ModuleType:
        if name == "broken_module":
            raise RuntimeError("Module initialization failed")
        return module

    monkeypatch.setattr(
        registry_module,
        "_iter_tool_module_names",
        lambda: ["broken_module", "valid_tool"],
    )
    monkeypatch.setattr(
        registry_module,
        "_import_tool_module",
        mock_import,
    )

    with caplog.at_level(logging.WARNING, logger="app.tools.registry"):
        tools = registry_module.get_registered_tools()

    tool_names = [t.name for t in tools]

    assert "valid_tool" in tool_names
    assert registry_module.get_registered_tool_map()["valid_tool"].run() == {"status": "ok"}

    # After #1464 the bare ``logger.warning(...)`` was replaced by
    # ``report_exception`` which routes generic exceptions at error severity,
    # so the surviving log record is at ERROR level.
    assert any(
        "Skipping broken_module" in record.message and record.levelname == "ERROR"
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# #1464 — surface tool-registry import-time failures to Sentry
# ---------------------------------------------------------------------------


class TestClassifyImportError:
    """Unit tests for ``_classify_import_error`` severity + tag selection."""

    def test_external_module_not_found_is_warning(self) -> None:
        exc = ModuleNotFoundError("No module named 'psycopg2'")
        exc.name = "psycopg2"
        severity, tags = registry_module._classify_import_error(exc)
        assert severity == "warning"
        assert tags == {
            "event": "optional_dependency_missing",
            "missing_module": "psycopg2",
        }

    def test_internal_module_not_found_is_error(self) -> None:
        """Internal ``app.*`` ModuleNotFoundError is our bug — capture at error."""
        exc = ModuleNotFoundError("No module named 'app.services.removed'")
        exc.name = "app.services.removed"
        severity, tags = registry_module._classify_import_error(exc)
        assert severity == "error"
        assert tags == {"event": "tool_module_import_failed"}

    def test_generic_exception_is_error(self) -> None:
        exc = RuntimeError("import side-effect blew up")
        severity, tags = registry_module._classify_import_error(exc)
        assert severity == "error"
        assert tags == {"event": "tool_module_import_failed"}

    def test_module_not_found_without_name_is_error(self) -> None:
        """Defensive: ModuleNotFoundError with no ``.name`` cannot be classified
        as an external optional-dep miss, so it falls through to ``error``."""
        exc = ModuleNotFoundError("synthetic — no name attribute")
        exc.name = None
        severity, tags = registry_module._classify_import_error(exc)
        assert severity == "error"
        assert tags["event"] == "tool_module_import_failed"


def _patch_registry_with_broken_module(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    """Stub the registry to expose exactly one tool module whose import raises ``exc``."""

    def mock_import(name: str) -> ModuleType:
        raise exc

    monkeypatch.setattr(
        registry_module,
        "_iter_tool_module_names",
        lambda: ["broken_module"],
    )
    monkeypatch.setattr(registry_module, "_import_tool_module", mock_import)


class TestImportFailuresReportToSentry:
    """``_load_registry_snapshot`` must route every import failure through
    ``report_exception`` (#1464), not silently warn — otherwise broken tool
    modules vanish from the agent's toolbox at runtime with no signal."""

    def test_external_dependency_missing_reports_as_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exc = ModuleNotFoundError("No module named 'boto3_extras'")
        exc.name = "boto3_extras"
        _patch_registry_with_broken_module(monkeypatch, exc)

        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            registry_module,
            "report_exception",
            lambda exc, **kwargs: calls.append({"exc": exc, **kwargs}),
        )
        registry_module._load_registry_snapshot()

        assert len(calls) == 1
        assert calls[0]["severity"] == "warning"
        tags = calls[0]["tags"]
        assert tags["surface"] == "tool"
        assert tags["module_name"] == "broken_module"
        assert tags["component"] == "app.tools.broken_module"
        assert tags["event"] == "optional_dependency_missing"
        assert tags["missing_module"] == "boto3_extras"

    def test_internal_import_failure_reports_as_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Internal ``app.*`` import failures must NOT be downgraded — they're
        always our bug."""
        exc = ModuleNotFoundError("No module named 'app.services.removed'")
        exc.name = "app.services.removed"
        _patch_registry_with_broken_module(monkeypatch, exc)

        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            registry_module,
            "report_exception",
            lambda exc, **kwargs: calls.append({"exc": exc, **kwargs}),
        )
        registry_module._load_registry_snapshot()

        assert len(calls) == 1
        assert calls[0]["severity"] == "error"
        assert calls[0]["tags"]["event"] == "tool_module_import_failed"

    def test_generic_exception_reports_as_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        exc = RuntimeError("import side-effect blew up")
        _patch_registry_with_broken_module(monkeypatch, exc)

        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            registry_module,
            "report_exception",
            lambda exc, **kwargs: calls.append({"exc": exc, **kwargs}),
        )
        registry_module._load_registry_snapshot()

        assert len(calls) == 1
        assert calls[0]["severity"] == "error"
        assert calls[0]["tags"]["event"] == "tool_module_import_failed"

    def test_sentry_tag_set_when_failures_occur(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``tools.import_failures`` Sentry tag is set so the issue surface
        can dashboard registry health across releases."""
        exc = RuntimeError("boom")
        _patch_registry_with_broken_module(monkeypatch, exc)

        # Swallow the real report_exception so it doesn't actually try to ship
        # to Sentry from a test process.
        monkeypatch.setattr(registry_module, "report_exception", lambda *_a, **_k: None)

        tags_set: list[tuple[str, str]] = []

        def fake_set_tag(key: str, value: str) -> None:
            tags_set.append((key, value))

        monkeypatch.setattr(registry_module.sentry_sdk, "set_tag", fake_set_tag)
        registry_module._load_registry_snapshot()

        assert ("tools.import_failures", "1") in tags_set

    def test_sentry_tag_always_set_on_clean_rebuild(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``tools.import_failures`` is rewritten on every rebuild — including
        with ``"0"`` — so a stale count from a previous load cannot bleed
        across ``clear_tool_registry_cache()`` calls and falsely flag a healthy
        process as still degraded."""
        monkeypatch.setattr(
            registry_module,
            "_iter_tool_module_names",
            lambda: [],
        )
        tags_set: list[tuple[str, str]] = []
        monkeypatch.setattr(
            registry_module.sentry_sdk,
            "set_tag",
            lambda k, v: tags_set.append((k, v)),
        )
        registry_module._load_registry_snapshot()

        assert ("tools.import_failures", "0") in tags_set

    def test_rebuild_after_failure_overwrites_stale_tag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a previously written ``tools.import_failures=N`` tag must
        be overwritten with ``"0"`` when a subsequent rebuild succeeds, so the
        stale failure count cannot continue to fire on healthy events."""
        exc = RuntimeError("boom")
        _patch_registry_with_broken_module(monkeypatch, exc)
        monkeypatch.setattr(registry_module, "report_exception", lambda *_a, **_k: None)

        tags_set: list[tuple[str, str]] = []
        monkeypatch.setattr(
            registry_module.sentry_sdk,
            "set_tag",
            lambda k, v: tags_set.append((k, v)),
        )

        registry_module.clear_tool_registry_cache()
        registry_module._load_registry_snapshot()
        registry_module.clear_tool_registry_cache()

        # Now flip the registry to a healthy state and rebuild.
        monkeypatch.setattr(registry_module, "_iter_tool_module_names", lambda: [])
        registry_module._load_registry_snapshot()

        # Final write must be "0", not the stale "1" from the first rebuild.
        registry_writes = [(k, v) for k, v in tags_set if k == "tools.import_failures"]
        assert registry_writes[-1] == ("tools.import_failures", "0")

    def test_sentry_tag_write_failure_does_not_break_registry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If ``sentry_sdk.set_tag`` raises (broken scope / SDK disabled mid-init),
        registry initialisation must still complete — but the failure must be
        logged at debug rather than silently swallowed by a bare
        ``suppress(Exception)``, so a misconfigured Sentry scope is observable."""
        monkeypatch.setattr(registry_module, "_iter_tool_module_names", lambda: [])

        def _raise(_key: str, _value: str) -> None:
            raise RuntimeError("sentry scope broken")

        monkeypatch.setattr(registry_module.sentry_sdk, "set_tag", _raise)

        with caplog.at_level("DEBUG", logger=registry_module.logger.name):
            registry_module._record_import_health(0)

        assert any(
            "tools.import_failures" in record.message and record.levelname == "DEBUG"
            for record in caplog.records
        )


def test_no_internal_app_imports_fail_on_default_extras() -> None:
    """Acceptance criterion: under the default extras matrix, no tool module
    in ``app/tools/`` should fail to import. If this test breaks, a real
    in-tree import is broken and a tool has silently disappeared from the
    agent's toolbox."""
    registry_module.clear_tool_registry_cache()
    failures: list[tuple[str, BaseException]] = []
    for module_name in registry_module._iter_tool_module_names():
        try:
            registry_module._import_tool_module(module_name)
        except ModuleNotFoundError as exc:
            # External optional-dependency miss is acceptable; only internal
            # ``app.*`` failures should fail this test.
            if exc.name and not exc.name.startswith("app."):
                continue
            failures.append((module_name, exc))
        except Exception as exc:  # pragma: no cover — surfaces real regressions
            failures.append((module_name, exc))
    assert not failures, "internal tool-module import failures detected: " + ", ".join(
        f"{name}: {exc!r}" for name, exc in failures
    )
