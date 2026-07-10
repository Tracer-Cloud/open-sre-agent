"""Named contract profiles for common monorepo layouts."""

from __future__ import annotations

from tools.architecture_issue_tool.scanners.import_graph.models import (
    ContractProfile,
    ForbiddenDirectRule,
    LayerContract,
)

_LAYERED_MONOREPO = LayerContract(
    name="layered-monorepo",
    roots=("src", "lib", "cmd", "internal", "packages"),
    layers=(
        ("infra", "platform", "internal", "pkg", "config", "core", "domain", "lib"),
        ("services", "tools", "integrations", "api"),
        ("app", "handlers", "cmd", "web", "ui", "surfaces", "gateway"),
    ),
    forbidden_direct=(
        ForbiddenDirectRule(source="infra", targets=("app", "api", "handlers", "surfaces")),
        ForbiddenDirectRule(source="internal", targets=("app", "api", "handlers")),
        ForbiddenDirectRule(source="core", targets=("surfaces", "gateway", "app")),
        ForbiddenDirectRule(source="platform", targets=("surfaces", "app", "api")),
    ),
    allowlist=(),
)

_PROFILES: dict[str, ContractProfile] = {
    "layered-monorepo": ContractProfile(name="layered-monorepo", contract=_LAYERED_MONOREPO),
}


def get_profile(name: str) -> ContractProfile | None:
    return _PROFILES.get(name)
