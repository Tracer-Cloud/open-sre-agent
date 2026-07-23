"""One place that turns collected credentials into a fully configured integration.

Setting an integration up happens on three surfaces — the onboarding wizard
(``opensre onboard``), ``opensre integrations setup <service>``, and the
interactive-shell action tools. Each one only differs in how it *collects*
values; what has to happen afterwards is identical:

1. every required field is present, and any cross-field rule holds (optional,
   integration-specific — see :attr:`IntegrationSetupSpec.validate`),
2. the credentials actually work (the integration's verifier),
3. references the user typed are resolved to what the runtime needs (optional,
   integration-specific — see :attr:`IntegrationSetupSpec.resolve`),
4. they are persisted to **every** tier that reads them — the integration
   store, the system keyring for secrets, and the project ``.env`` for the rest,
   and
5. any post-save side effect runs (optional — see
   :attr:`IntegrationSetupSpec.finalize`).

Before this module each surface reimplemented step 4, and they disagreed: the
wizard wrote all three tiers while ``integrations setup`` wrote only the store.
Runtime resolution hides that (it checks the store first), but anything reading
the environment — notably the deploy preflight in
``platform/deployment/ecr_deploy/prep.py`` — sees a half-configured integration.

Callers now describe *what* the integration needs with an
:class:`IntegrationSetupSpec` and hand collected values to :func:`apply_setup`;
where each value lands is decided here, once.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from config.env_file import is_sensitive_env_key, sync_env_secret, sync_env_values
from integrations.store import upsert_integration
from integrations.verification import VerifierFn


@dataclass(frozen=True)
class ResolvedCredentials:
    """Output of an integration's optional resolve step.

    A non-empty *error* aborts setup; *note* is appended to the success detail
    so the user sees what a typed reference turned into.
    """

    credentials: dict[str, str | None]
    note: str = ""
    error: str = ""


ResolveFn = Callable[[dict[str, str | None]], ResolvedCredentials]

# Cross-field check run before verification. Returns "" when the credentials are
# acceptable, or a user-facing sentence explaining why they are not — used for
# constraints ``SetupField.required`` cannot express, like "one of these groups
# must be complete".
ValidateFn = Callable[[dict[str, str | None]], str]

# Side effect run after the credentials are persisted, for setup that has to
# reach past the store (registering a webhook, a slash command, …). Returns an
# optional note appended to the success detail; it is best-effort by contract —
# the integration is already saved, so a failure here is surfaced, not unwound.
FinalizeFn = Callable[[dict[str, str | None]], str]


@dataclass(frozen=True)
class SetupField:
    """One credential an integration needs, and where it is persisted."""

    name: str
    """Key under the integration store's ``credentials`` mapping."""

    label: str
    """Human-readable field name, used in "X is required" errors."""

    prompt: str = ""
    """Question text when collecting this field interactively; defaults to *label*.

    Kept separate so the question can carry guidance ("Default chat ID or
    @channelname") while the error stays terse ("Default chat ID is required.").
    """

    env_var: str | None = None
    """Env var this field mirrors, or ``None`` to keep it store-only.

    The tier is derived from the name — :func:`config.env_file.is_sensitive_env_key`
    routes ``*_TOKEN``/``*_KEY``/``*_PASSWORD`` to the keyring and everything
    else to ``.env``. Fields do not get to choose.
    """

    default: str = ""
    """Value to use when the field is submitted blank.

    Applied in :func:`apply_setup`, not just offered as a prompt prefill, so a
    surface that never prompts — the wizard reusing a stored value, an agent
    filling fields from a conversation — lands on the same credentials as
    someone pressing enter at the CLI. A field with a default is therefore
    never missing, whatever *required* says.
    """

    required: bool = True
    """When true, a blank value fails setup instead of being stored as ``None``."""

    secret: bool = False
    """Whether collection surfaces should mask this field while it is typed."""

    constant: str | None = None
    """Fixed value that is always persisted and never prompted.

    When set, collection surfaces skip this field and :func:`apply_setup`
    ignores any submitted value under *name*. Use for transport modes and
    other values the user must not choose — OpenClaw's ``stdio`` mode, for
    example, whose config-model default is ``streamable-http``.
    """

    @property
    def question(self) -> str:
        """The text to prompt with."""
        return self.prompt or self.label

    @property
    def is_constant(self) -> bool:
        """True when this field is fixed rather than collected."""
        return self.constant is not None


@dataclass(frozen=True)
class IntegrationSetupSpec:
    """The full set of credentials one integration is configured with."""

    service: str
    fields: tuple[SetupField, ...]

    validate: ValidateFn | None = None
    """Optional cross-field check, run after collection and before verification.

    ``SetupField.required`` only expresses "this one field is mandatory". Use
    this for constraints that span fields — Slack accepts a webhook URL *or*
    the bot/app token pair, which is neither field being individually required.
    Mark those fields ``required=False`` and enforce the real rule here. Runs
    before the verifier so an impossible combination fails without a network call.
    """

    verify: VerifierFn | None = None
    """The integration's verifier, or ``None`` to skip verification.

    Wired explicitly rather than looked up in the verifier registry: the caller
    already knows which integration it is configuring, and an explicit reference
    keeps the flow free of import-time registration ordering.
    """

    resolve: ResolveFn | None = None
    """Optional post-verification rewrite of the collected credentials.

    Runs after the verifier (so it can rely on the credentials being valid) and
    before anything is persisted. Use it when what the user can reasonably type
    is not what the runtime should store — Telegram's ``@channelname`` becoming
    a numeric chat id, for instance.
    """

    finalize: FinalizeFn | None = None
    """Optional side effect run after the credentials are persisted.

    For setup that has to reach past the store once the integration exists —
    Discord registering its ``/investigate`` slash command, for example. Runs
    last so it can assume a saved, verified integration, and is best-effort: it
    returns a note for the success detail and a failure is surfaced there rather
    than rolling back the save.
    """


@dataclass(frozen=True)
class SetupOutcome:
    """What :func:`apply_setup` did, in a shape every surface can render."""

    ok: bool
    """True only when the credentials were verified *and* persisted."""

    detail: str
    """Renderable sentence for the user — the verifier's message, or why it failed."""

    env_path: Path | None = None
    """The ``.env`` written; always set when *ok*, never set otherwise."""


def _collect_credentials(
    spec: IntegrationSetupSpec, values: Mapping[str, str | None]
) -> tuple[dict[str, str | None], str]:
    """Normalize submitted values, or return the first missing required field.

    The spec is authoritative: fields it declares are the credentials that get
    stored, and anything else in *values* is ignored. Constant fields always
    take their fixed value, even when *values* supplies something else.
    """
    credentials: dict[str, str | None] = {}
    for field in spec.fields:
        if field.is_constant:
            # Keep "" as "" — OpenClaw's empty url/auth_token are intentional.
            credentials[field.name] = field.constant
            continue
        value = (values.get(field.name) or "").strip() or field.default
        if not value and field.required:
            return {}, f"{field.label} is required."
        credentials[field.name] = value or None
    return credentials, ""


def _verify(spec: IntegrationSetupSpec, credentials: dict[str, str | None]) -> tuple[bool, str]:
    """Run the spec's verifier against *credentials*.

    An integration with no verifier is treated as verified — the alternative
    would be refusing to configure it at all. Verifiers take ``dict[str, str]``,
    so unset optional fields are dropped rather than passed as ``None``.
    """
    if spec.verify is None:
        return True, ""
    probe = {name: value for name, value in credentials.items() if value}
    outcome = spec.verify("setup", probe)
    return outcome["status"] == "passed", outcome["detail"]


def _persist_env(spec: IntegrationSetupSpec, credentials: dict[str, str | None]) -> Path:
    """Mirror env-backed fields into the keyring / ``.env`` and return the ``.env`` path.

    ``.env`` is rewritten even when no field targets it: the rewrite also strips
    any stale secret assignments left by an older setup (see
    :func:`config.env_file.sync_env_values`).

    Raises whatever the writers raise — notably ``PermissionError`` from
    :func:`config.env_file.write_env_lines` on an unwritable ``.env``.
    """
    env_values: dict[str, str] = {}
    for field in spec.fields:
        if not field.env_var:
            continue
        # Blank values are written through rather than skipped, so clearing an
        # optional field clears every tier. Skipping would leave the previous
        # value in ``.env`` while the store recorded ``None``, and credential
        # resolution falls back to the environment when the store is empty — so
        # the value the user just cleared would keep resolving.
        value = credentials.get(field.name) or ""
        if is_sensitive_env_key(field.env_var):
            sync_env_secret(field.env_var, value)
        else:
            env_values[field.env_var] = value
    return sync_env_values(env_values)


def apply_setup(
    spec: IntegrationSetupSpec,
    values: Mapping[str, str | None],
) -> SetupOutcome:
    """Validate, verify, and persist an integration's credentials to every tier.

    Nothing is written unless verification passes, so a rejected credential
    never leaves a half-configured integration behind.
    """
    credentials, missing = _collect_credentials(spec, values)
    if missing:
        return SetupOutcome(ok=False, detail=missing)

    if spec.validate is not None:
        invalid = spec.validate(credentials)
        if invalid:
            return SetupOutcome(ok=False, detail=invalid)

    verified, detail = _verify(spec, credentials)
    if not verified:
        return SetupOutcome(ok=False, detail=detail)

    if spec.resolve is not None:
        resolved = spec.resolve(credentials)
        if resolved.error:
            return SetupOutcome(ok=False, detail=resolved.error)
        credentials = resolved.credentials
        detail = " ".join(part for part in (detail, resolved.note) if part)

    # Env/keyring before the store, so no failure can leave the store-only state
    # this module exists to prevent. If the env write fails nothing is persisted
    # at all; if the store write failed afterwards, credential resolution still
    # falls back to the environment, so the integration keeps working.
    try:
        env_path = _persist_env(spec, credentials)
    except (OSError, RuntimeError) as exc:
        # A local CLI/wizard surface, so the detail may name the actual cause
        # (usually an unwritable .env). Nothing has been persisted at this point.
        return SetupOutcome(ok=False, detail=f"Could not save {spec.service} credentials: {exc}")

    upsert_integration(spec.service, {"credentials": credentials})

    if spec.finalize is not None:
        note = spec.finalize(credentials)
        detail = " ".join(part for part in (detail, note) if part)

    return SetupOutcome(ok=True, detail=detail, env_path=env_path)


__all__ = [
    "FinalizeFn",
    "IntegrationSetupSpec",
    "ResolveFn",
    "ResolvedCredentials",
    "SetupField",
    "SetupOutcome",
    "ValidateFn",
    "apply_setup",
]
