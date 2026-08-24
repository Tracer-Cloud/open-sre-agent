"""``opensre remote-sync`` — thin Click adapter over :mod:`infrastructure.filestorage`."""

from __future__ import annotations

from contextlib import suppress

import click
from prompt_toolkit import prompt as pt_prompt

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
    REMOTE_SYNC_PASSPHRASE_ENV,
)
from config.secrets.store import normalize_secret
from infrastructure.filestorage import (
    MissingPassphraseError,
    RemoteSyncConfigError,
    RemoteSyncError,
)
from infrastructure.filestorage.encryption.keys import resolve_passphrase, save_passphrase
from infrastructure.filestorage.enums import RemoteSyncField, RemoteSyncSubcommand
from infrastructure.filestorage.messages import (
    DISABLED_HELP,
    ROTATE_CONFIRM,
    SETUP_DISABLED_CONFIRM,
    format_reencrypt_lines,
    format_report_lines,
    format_setup_lines,
    format_status_lines,
)
from infrastructure.filestorage.operations import (
    get_sync_status,
    reencrypt_remote_store,
    rotate_remote_sync_passphrase,
    run_remote_sync,
)
from infrastructure.filestorage.providers.registry import builtin_providers, provider_extra_fields
from infrastructure.filestorage.setup import (
    RemoteSyncSetupRequest,
    disable_remote_sync,
    save_remote_sync_settings,
)
from infrastructure.process.exit_codes import ERROR, SUCCESS
from surfaces.cli.commands.remote_sync_progress import CliProgress
from surfaces.cli.telemetry import capture_exception


@click.group(name="remote-sync", invoke_without_command=True)
@click.pass_context
def remote_sync_command(ctx: click.Context) -> None:
    """Mirror sessions and memory to your own object store."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(status_command)


@remote_sync_command.command(name=RemoteSyncSubcommand.STATUS.value)
def status_command() -> None:
    """Show whether sync is on, and what would be mirrored."""
    try:
        lines = format_status_lines(get_sync_status())
    except RemoteSyncError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc
    for line in lines:
        click.echo(line)
    raise SystemExit(SUCCESS)


@remote_sync_command.command(name=RemoteSyncSubcommand.SYNC.value)
@click.option("--pull-only", is_flag=True, help="Download only; send nothing.")
@click.option("--push-only", is_flag=True, help="Upload only; fetch nothing.")
@click.option("--dry-run", is_flag=True, help="Preview transfers without changing anything.")
def sync_now_command(pull_only: bool, push_only: bool, dry_run: bool) -> None:
    """Sync now: pull remote changes, then push local ones."""
    progress = CliProgress() if click.get_text_stream("stdout").isatty() else None
    try:
        report = run_remote_sync(
            pull_only=pull_only, push_only=push_only, dry_run=dry_run, on_progress=progress
        )
    except RemoteSyncError as exc:
        if progress is not None:
            progress.close()
        click.echo(f"Sync failed: {exc}", err=True)
        raise SystemExit(ERROR) from exc
    finally:
        if progress is not None:
            progress.close()
    if report is None:
        click.echo(DISABLED_HELP)
        raise SystemExit(SUCCESS)
    for line in format_report_lines(report, dry_run=dry_run):
        click.echo(line)
    raise SystemExit(SUCCESS)


def run_remote_sync_on_exit() -> None:
    """Run remote sync without changing the interactive shell's exit result."""
    try:
        report = run_remote_sync()
    except Exception as exc:  # noqa: BLE001 - an optional exit hook must fail soft
        with suppress(Exception):
            capture_exception(exc, context="surfaces.cli.sync_on_exit")
        click.echo(
            "Automatic remote sync failed; run 'opensre remote-sync sync' for details.",
            err=True,
        )
        return

    if report is None:
        click.echo(
            "Automatic remote sync skipped because remote sync is off; "
            "run 'opensre remote-sync setup' first.",
            err=True,
        )
        return
    for line in format_report_lines(report):
        click.echo(line)


@remote_sync_command.command(name=RemoteSyncSubcommand.SETUP.value)
@click.option(
    "--provider",
    default=None,
    help=f"Backend name (default {DEFAULT_REMOTE_SYNC_PROVIDER}; built-in: {', '.join(builtin_providers())}).",
)
@click.option(
    "--bucket",
    default=None,
    help="Store name you own (S3 bucket, GCS bucket, or Blob store id).",
)
@click.option(
    "--prefix",
    default=None,
    help=f"Key prefix (default {DEFAULT_REMOTE_SYNC_PREFIX}).",
)
@click.option("--region", default=None, help="Region override when the provider supports it.")
@click.option("--profile", default=None, help="Named credentials profile (AWS).")
@click.option(
    "--enabled/--disabled",
    default=True,
    show_default=True,
    help="Whether remote sync is switched on in stored settings. --disabled alone only turns it off.",
)
@click.option(
    "--encrypt/--no-encrypt",
    default=None,
    help=(
        "Encrypt contents under a passphrase before upload. Prompts for the "
        "passphrase. Asked interactively when neither form is given."
    ),
)
def setup_command(
    provider: str | None,
    bucket: str | None,
    prefix: str | None,
    region: str | None,
    profile: str | None,
    enabled: bool,
    encrypt: bool | None,
) -> None:
    """Write remote_sync settings to ~/.opensre/config.yml (interactive if flags omitted)."""
    if not enabled and (bucket is None or not bucket.strip()):
        # --disabled with no new settings just switches the stored section off.
        try:
            _reject_disabled_with_setup_flags(
                provider=provider,
                prefix=prefix,
                region=region,
                profile=profile,
                encrypt=encrypt,
            )
            disable_remote_sync()
        except RemoteSyncError as exc:
            click.echo(str(exc), err=True)
            raise SystemExit(ERROR) from exc
        click.echo(SETUP_DISABLED_CONFIRM)
        raise SystemExit(SUCCESS)
    try:
        request = _collect_setup_request(
            provider=provider,
            bucket=bucket,
            prefix=prefix,
            region=region,
            profile=profile,
            enabled=enabled,
            encrypted=encrypt,
        )
        if request.encrypted:
            # Read off the request, not the flag: the interactive path may have
            # asked. Before the settings are written, because a machine left
            # claiming to encrypt with no passphrase would refuse every sync.
            _ensure_passphrase()
        config = save_remote_sync_settings(request)
    except (RemoteSyncError, click.Abort) as exc:
        if isinstance(exc, click.Abort):
            raise SystemExit(ERROR) from exc
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc
    for line in format_setup_lines(config, enabled=request.enabled):
        click.echo(line)
    raise SystemExit(SUCCESS)


def _reject_disabled_with_setup_flags(
    *,
    provider: str | None,
    prefix: str | None,
    region: str | None,
    profile: str | None,
    encrypt: bool | None,
) -> None:
    """``--disabled`` only flips the switch; explicit setup values need a bucket."""
    given = [
        name
        for name, value in (
            ("provider", provider),
            ("prefix", prefix),
            ("region", region),
            ("profile", profile),
        )
        if value is not None and value.strip() != ""
    ]
    if encrypt is not None:
        # One option, two spellings: name back the one that was actually typed.
        given.append("encrypt" if encrypt else "no-encrypt")
    if given:
        flags = ", ".join(f"--{name}" for name in given)
        raise RemoteSyncConfigError(
            f"--disabled without --bucket only switches sync off; it cannot also set {flags}. "
            "Pass --bucket to save new settings, or drop the extra flags."
        )


def _collect_setup_request(
    *,
    provider: str | None,
    bucket: str | None,
    prefix: str | None,
    region: str | None,
    profile: str | None,
    enabled: bool,
    encrypted: bool | None = None,
) -> RemoteSyncSetupRequest:
    """Use flags when complete; otherwise prompt on a TTY.

    Supplying ``--provider`` and ``--bucket`` skips the questions about *those*
    values; it does not make the run unattended. The encryption question is
    therefore asked on both paths — see :func:`_resolve_encrypt` — because
    otherwise the shortest documented way to set sync up is also the one that
    never mentions the store will hold readable incident history.
    """
    flags_complete = provider is not None and bucket is not None and str(bucket).strip() != ""
    if flags_complete:
        return RemoteSyncSetupRequest(
            bucket=str(bucket),
            provider=str(provider),
            prefix=prefix if prefix is not None else DEFAULT_REMOTE_SYNC_PREFIX,
            region=region or "",
            profile=profile or "",
            enabled=enabled,
            encrypted=_resolve_encrypt(encrypted),
        )

    if not click.get_text_stream("stdin").isatty():
        raise RemoteSyncError(
            "pass --provider and --bucket, or run setup in an interactive terminal"
        )

    bucket_value = click.prompt(
        "Bucket / store name", default=bucket or "", show_default=bool(bucket)
    )
    provider_value = click.prompt(
        f"Provider ({', '.join(builtin_providers())}, …)",
        default=provider or DEFAULT_REMOTE_SYNC_PROVIDER,
        show_default=True,
    )
    prefix_value = click.prompt(
        "Prefix", default=prefix or DEFAULT_REMOTE_SYNC_PREFIX, show_default=True
    )
    # Bound before the request is built so the questions come in the order they
    # are read: the provider's own fields, then the one security choice.
    region_value = _prompt_extra_field(RemoteSyncField.REGION, provider_value, region)
    profile_value = _prompt_extra_field(RemoteSyncField.PROFILE, provider_value, profile)
    encrypted_value = _resolve_encrypt(encrypted)
    return RemoteSyncSetupRequest(
        bucket=bucket_value,
        provider=provider_value,
        prefix=prefix_value,
        region=region_value,
        profile=profile_value,
        enabled=enabled,
        encrypted=encrypted_value,
    )


def _resolve_encrypt(encrypted: bool | None) -> bool:
    """Settle the encryption choice: the flag, else ask, else off.

    ``None`` means neither ``--encrypt`` nor ``--no-encrypt`` was given. A
    terminal gets asked; a pipe or a CI job does not, because a question nobody
    can answer would hang the run — those keep the old default and can state
    the choice with the flag.
    """
    if encrypted is not None:
        return encrypted
    if not click.get_text_stream("stdin").isatty():
        return False
    return _prompt_encrypt()


def _prompt_encrypt() -> bool:
    """Offer encryption, stating what declining costs and what accepting risks.

    Defaults to no: turning it on means a passphrase whose loss destroys the
    store's contents, and that is not a thing to opt someone into by default.
    Both halves are said out loud so the default is a decision rather than the
    path of least resistance.
    """
    click.echo(
        "\nEncrypt contents before upload? Without it, whoever operates the store "
        "can read your sessions and memory.\nWith it, you choose a passphrase — "
        "lose that passphrase and the store's contents are unrecoverable."
    )
    return bool(click.confirm("Encrypt contents", default=False))


def _prompt_extra_field(field: RemoteSyncField, provider: str, current: str | None) -> str:
    """Prompt for ``field`` only if ``provider`` declared it; otherwise leave it unset.

    The declaration (and its prompt text) lives with the provider — see
    :func:`infrastructure.filestorage.providers.registry.provider_extra_fields` —
    so this stays generic across every registered provider, built-in or
    community, instead of hardcoding which providers use which field.
    """
    declared = {extra.field: extra for extra in provider_extra_fields(provider)}
    extra = declared.get(field)
    if extra is None:
        return current or ""
    return str(click.prompt(extra.prompt, default=current or "", show_default=False))


def _ensure_passphrase() -> None:
    """Guarantee a passphrase exists, asking for one only if none does.

    A machine joining an existing store already has it — exported, in the
    keychain, or in the fallback file — and asking again would invite a typo
    that silently points this machine at a different key.
    """
    try:
        resolve_passphrase()
    except MissingPassphraseError:
        save_passphrase(_prompt_new_passphrase("Encryption passphrase"))


def _ask_masked(label: str) -> str:
    """Read one line, echoing ``*`` per character.

    ``prompt_toolkit`` rather than ``click.prompt(hide_input=True)``, which
    echoes nothing at all: on a prompt typed twice and never displayed, no
    feedback leaves the user unable to tell a swallowed keystroke from a
    working one. Raw ``prompt_toolkit`` rather than ``questionary`` because
    this sits among plain click prompts and questionary would bring its own
    ``?`` styling to one question in the middle of the flow.
    """
    try:
        return str(pt_prompt(f"{label}: ", is_password=True))
    except (KeyboardInterrupt, EOFError) as exc:
        # Same outcome as aborting any other prompt in this command.
        raise click.Abort from exc


def _prompt_new_passphrase(label: str) -> str:
    """Ask for a passphrase twice, refusing to invent one off a TTY.

    Losing this passphrase means losing the store's contents, so it is never
    defaulted, generated, or read from a flag — a flag would put it in shell
    history for every machine that ever set the store up. Mismatches re-ask
    rather than abort: the typo is the likely cause, and making the user re-run
    setup for it teaches nothing.

    Returns the normalized form, which is what the secret tiers will hand back
    later. Callers wrap the remote store under this value, so returning the
    keystrokes verbatim would seal it under a passphrase this machine could
    never resolve again. Normalizing before the comparison too: two entries
    differing only in invisible padding are the same passphrase once stored, so
    re-asking would be asking the user to reproduce a difference that cannot
    survive.
    """
    if not click.get_text_stream("stdin").isatty():
        raise RemoteSyncError(
            f"a passphrase is needed and there is no terminal to ask on. "
            f"Export {REMOTE_SYNC_PASSPHRASE_ENV} for this command instead."
        )
    while True:
        passphrase = normalize_secret(_ask_masked(label))
        if not passphrase:
            raise RemoteSyncError("passphrase cannot be empty")
        if passphrase == normalize_secret(_ask_masked(f"{label} (again)")):
            return passphrase
        click.echo("The two entries did not match — try again.", err=True)


@remote_sync_command.command(name=RemoteSyncSubcommand.ROTATE_PASSPHRASE.value)
def rotate_passphrase_command() -> None:
    """Change the passphrase. Instant — no object is re-uploaded."""
    try:
        old = resolve_passphrase()
        new = _prompt_new_passphrase("New encryption passphrase")
        rotate_remote_sync_passphrase(old_passphrase=old, new_passphrase=new)
    except (RemoteSyncError, click.Abort) as exc:
        # Nothing was rotated, so the old passphrase still opens the store.
        if isinstance(exc, click.Abort):
            raise SystemExit(ERROR) from exc
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc

    # The store has moved, and saying so before the local half can fail is what
    # keeps a storage problem from reading as "the rotation did not happen" —
    # re-running on that belief would offer the old passphrase to a store that
    # no longer takes it.
    click.echo(ROTATE_CONFIRM)
    try:
        # Only after the store accepted it: storing a passphrase the store
        # never adopted would lock this machine out of its own history.
        save_passphrase(new)
    except RemoteSyncError as exc:
        click.echo(f"\n{exc}", err=True)
        raise SystemExit(ERROR) from exc
    raise SystemExit(SUCCESS)


@remote_sync_command.command(name=RemoteSyncSubcommand.REENCRYPT.value)
def reencrypt_command() -> None:
    """Reseal every stored object under a fresh key (re-uploads everything)."""
    try:
        report = reencrypt_remote_store(passphrase=resolve_passphrase())
    except RemoteSyncError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc
    for line in format_reencrypt_lines(report):
        click.echo(line)
    raise SystemExit(SUCCESS)


__all__ = ["remote_sync_command", "run_remote_sync_on_exit"]
