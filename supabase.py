[35mapp/agent/investigation.py[m[36m:[m    "[1;31msupabase[m": ["[1;31msupabase[m"],
[35mapp/agent/prompt.py[m[36m:[m    "[1;31msupabase[m": ["[1;31msupabase[m"],
[35mapp/cli/interactive_shell/ui/banner.py[m[36m:[m    "[1;31msupabase[m": "[1;31mSupabase[m",
[35mapp/integrations/_catalog_impl.py[m[36m:[mfrom app.integrations.[1;31msupabase[m import build_[1;31msupabase[m_config
[35mapp/integrations/_catalog_impl.py[m[36m:[m    if key == "[1;31msupabase[m":
[35mapp/integrations/_catalog_impl.py[m[36m:[m            sb_config = build_[1;31msupabase[m_config(
[35mapp/integrations/_catalog_impl.py[m[36m:[m            }, "[1;31msupabase[m"
[35mapp/integrations/_catalog_impl.py[m[36m:[m    [1;31msupabase[m_url = os.getenv("[1;31mSUPABASE[m_URL", "").strip()
[35mapp/integrations/_catalog_impl.py[m[36m:[m    [1;31msupabase[m_service_key = os.getenv("[1;31mSUPABASE[m_SERVICE_KEY", "").strip()
[35mapp/integrations/_catalog_impl.py[m[36m:[m    if [1;31msupabase[m_url and [1;31msupabase[m_service_key:
[35mapp/integrations/_catalog_impl.py[m[36m:[m            sb_config = build_[1;31msupabase[m_config(
[35mapp/integrations/_catalog_impl.py[m[36m:[m                {"url": [1;31msupabase[m_url, "service_key": [1;31msupabase[m_service_key}
[35mapp/integrations/_catalog_impl.py[m[36m:[m                    "[1;31msupabase[m",
[35mapp/integrations/_catalog_impl.py[m[36m:[m            _report_env_loader_failure(exc, integration="[1;31msupabase[m")
[35mapp/integrations/_verification_adapters.py[m[36m:[mfrom app.integrations.[1;31msupabase[m import build_[1;31msupabase[m_config, validate_[1;31msupabase[m_config
[35mapp/integrations/_verification_adapters.py[m[36m:[mdef _verify_[1;31msupabase[m(service: str, config: dict[str, Any]) -> dict[str, str]:
[35mapp/integrations/_verification_adapters.py[m[36m:[m        "[1;31msupabase[m",
[35mapp/integrations/_verification_adapters.py[m[36m:[m        build_config=build_[1;31msupabase[m_config,
[35mapp/integrations/_verification_adapters.py[m[36m:[m        validate_config=validate_[1;31msupabase[m_config,
[35mapp/integrations/_verification_adapters.py[m[36m:[m    "_verify_[1;31msupabase[m",
[35mapp/integrations/registry.py[m[36m:[m    _verify_[1;31msupabase[m,
[35mapp/integrations/registry.py[m[36m:[m        service="[1;31msupabase[m",
[35mapp/integrations/registry.py[m[36m:[m        verifier=_verify_[1;31msupabase[m,
[35mapp/integrations/supabase.py[m[36m:[m"""Shared [1;31mSupabase[m integration helpers.
[35mapp/integrations/supabase.py[m[36m:[mqueries for [1;31mSupabase[m projects. Covers the PostgREST API, Auth service, and
[35mapp/integrations/supabase.py[m[36m:[mfrom app.services.[1;31msupabase[m.client import [1;31msupabase[m_http_get
[35mapp/integrations/supabase.py[m[36m:[mDEFAULT_[1;31mSUPABASE[m_TIMEOUT_SECONDS = 10.0
[35mapp/integrations/supabase.py[m[36m:[mDEFAULT_[1;31mSUPABASE[m_MAX_RESULTS = 50
[35mapp/integrations/supabase.py[m[36m:[mclass [1;31mSupabase[mConfig(StrictConfigModel):
[35mapp/integrations/supabase.py[m[36m:[m    """Normalized [1;31mSupabase[m connection settings."""
[35mapp/integrations/supabase.py[m[36m:[m    timeout_seconds: float = Field(default=DEFAULT_[1;31mSUPABASE[m_TIMEOUT_SECONDS, gt=0)
[35mapp/integrations/supabase.py[m[36m:[m    max_results: int = Field(default=DEFAULT_[1;31mSUPABASE[m_MAX_RESULTS, gt=0, le=200)
[35mapp/integrations/supabase.py[m[36m:[mclass [1;31mSupabase[mValidationResult:
[35mapp/integrations/supabase.py[m[36m:[m    """Result of validating a [1;31mSupabase[m integration."""
[35mapp/integrations/supabase.py[m[36m:[mdef build_[1;31msupabase[m_config(raw: dict[str, Any] | None) -> [1;31mSupabase[mConfig:
[35mapp/integrations/supabase.py[m[36m:[m    """Build a normalized [1;31mSupabase[m config object from raw data."""
[35mapp/integrations/supabase.py[m[36m:[m    return [1;31mSupabase[mConfig.model_validate(raw or {})
[35mapp/integrations/supabase.py[m[36m:[mdef [1;31msupabase[m_config_from_env() -> [1;31mSupabase[mConfig | None:
[35mapp/integrations/supabase.py[m[36m:[m    """Load a [1;31mSupabase[m config from environment variables."""
[35mapp/integrations/supabase.py[m[36m:[m    url = os.getenv("[1;31mSUPABASE[m_URL", "").strip()
[35mapp/integrations/supabase.py[m[36m:[m    service_key = os.getenv("[1;31mSUPABASE[m_SERVICE_KEY", "").strip()
[35mapp/integrations/supabase.py[m[36m:[m    return build_[1;31msupabase[m_config({"url": url, "service_key": service_key})
[35mapp/integrations/supabase.py[m[36m:[mdef resolve_[1;31msupabase[m_config(project_url: str) -> [1;31mSupabase[mConfig:
[35mapp/integrations/supabase.py[m[36m:[m    or if the URL origin doesn't match any configured [1;31mSupabase[m integration.
[35mapp/integrations/supabase.py[m[36m:[m            if str(record.get("service", "")).lower() != "[1;31msupabase[m":
[35mapp/integrations/supabase.py[m[36m:[m                    return build_[1;31msupabase[m_config({"url": normalized, "service_key": service_key})
[35mapp/integrations/supabase.py[m[36m:[m            "[1;31mSupabase[m credential store lookup failed; falling back to environment",
[35mapp/integrations/supabase.py[m[36m:[m    env_config = [1;31msupabase[m_config_from_env()
[35mapp/integrations/supabase.py[m[36m:[m            "[1;31mSupabase[m is not configured. "
[35mapp/integrations/supabase.py[m[36m:[m            "Register the integration via the UI or set [1;31mSUPABASE[m_URL and [1;31mSUPABASE[m_SERVICE_KEY."
[35mapp/integrations/supabase.py[m[36m:[m            f"[1;31mSUPABASE[m_URL origin. Refusing to attach credentials to an "
[35mapp/integrations/supabase.py[m[36m:[m    return build_[1;31msupabase[m_config({"url": normalized, "service_key": env_config.service_key})
[35mapp/integrations/supabase.py[m[36m:[m    config: [1;31mSupabase[mConfig,
[35mapp/integrations/supabase.py[m[36m:[m    """Make a GET request to the [1;31mSupabase[m project API.
[35mapp/integrations/supabase.py[m[36m:[m    return [1;31msupabase[m_http_get(
[35mapp/integrations/supabase.py[m[36m:[mdef validate_[1;31msupabase[m_config(config: [1;31mSupabase[mConfig) -> [1;31mSupabase[mValidationResult:
[35mapp/integrations/supabase.py[m[36m:[m    """Validate [1;31mSupabase[m connectivity by probing the PostgREST root endpoint."""
[35mapp/integrations/supabase.py[m[36m:[m        return [1;31mSupabase[mValidationResult(ok=False, detail="[1;31mSupabase[m URL is required.")
[35mapp/integrations/supabase.py[m[36m:[m        return [1;31mSupabase[mValidationResult(ok=False, detail="[1;31mSupabase[m service key is required.")
[35mapp/integrations/supabase.py[m[36m:[m            return [1;31mSupabase[mValidationResult(
[35mapp/integrations/supabase.py[m[36m:[m                detail=f"Connected to [1;31mSupabase[m project at {config.url}.",
[35mapp/integrations/supabase.py[m[36m:[m        return [1;31mSupabase[mValidationResult(
[35mapp/integrations/supabase.py[m[36m:[m            detail=f"[1;31mSupabase[m PostgREST returned HTTP {status}.",
[35mapp/integrations/supabase.py[m[36m:[m        return [1;31mSupabase[mValidationResult(ok=False, detail=f"[1;31mSupabase[m connection failed: {err}")
[35mapp/integrations/supabase.py[m[36m:[mdef [1;31msupabase[m_is_available(sources: dict[str, dict]) -> bool:  # type: ignore[type-arg]
[35mapp/integrations/supabase.py[m[36m:[m    """Check if [1;31mSupabase[m integration identifying params are present."""
[35mapp/integrations/supabase.py[m[36m:[m    sb = sources.get("[1;31msupabase[m", {})
[35mapp/integrations/supabase.py[m[36m:[mdef [1;31msupabase[m_extract_params(sources: dict[str, dict]) -> dict[str, Any]:  # type: ignore[type-arg]
[35mapp/integrations/supabase.py[m[36m:[m    """Extract [1;31mSupabase[m identifying params from resolved 