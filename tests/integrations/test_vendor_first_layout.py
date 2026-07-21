"""T-1 vendor-first layout invariant.

Every vendor integration must live in a package
(``integrations/<vendor>/`` with ``config``/``client``/``verifier``/``tools``
as needed), never as a flat ``integrations/<vendor>.py`` module. Only the
shared cross-cutting infra modules below are allowed to sit flat under
``integrations/``.

This guards the T-18 completion: a future PR that reintroduces a flat vendor
module (the pattern C-14/C-15/C-16 and this migration removed) fails here.
"""

from __future__ import annotations

from pathlib import Path

INTEGRATIONS_DIR = Path(__file__).resolve().parents[2] / "integrations"

# Cross-cutting infra that is intentionally flat — NOT vendor integrations.
# `port.py` and `harness_adapters.py` are Ports & Adapters wiring, not SaaS
# vendors.
ALLOWED_FLAT_MODULES = frozenset(
    {
        "catalog.py",
        "cli.py",
        "config_models.py",
        "daily_update.py",
        "effective_models.py",
        "harness_adapters.py",
        "mcp_streamable_http_compat.py",
        "messaging_security.py",
        "models.py",
        "port.py",
        "probes.py",
        "registry.py",
        "scheduled_agent_bootstrap.py",
        "selectors.py",
        "store.py",
        "verify.py",
        # Cross-cutting credential-resolution infra (fetches every vendor's org
        # creds from the webapp vault), not a vendor — like store.py / registry.py.
        "webapp_vault.py",
    }
)


def test_no_flat_vendor_modules() -> None:
    flat = {
        p.name
        for p in INTEGRATIONS_DIR.glob("*.py")
        if not p.name.startswith("_")  # skip dunder + private infra helpers
    }
    stray = sorted(flat - ALLOWED_FLAT_MODULES)
    assert not stray, (
        "Flat vendor modules found under integrations/: "
        f"{stray}. Move each into a vendor package "
        "(integrations/<vendor>/ with config.py/client.py/verifier.py), "
        "delete the flat module, and re-export from __init__.py (T-18)."
    )
