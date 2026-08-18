"""What Buzz needs before it is considered configured.

``private_key`` (the agent's Nostr identity) is the only required field —
every other field defaults sensibly (``relay_url`` to the local dev relay,
``buzz_path`` to ``buzz`` on ``PATH``). :func:`integrations.buzz.verifier.verify_buzz`
enforces the real rule (private key present, ``buzz`` binary resolvable, relay
reachable) so setup and health checks agree on what "configured" means.

Buzz channels are identified by UUID (see ``buzz channels list``), not by
name — the setup prompt says so explicitly since every other messaging
integration in this repo takes a #name or numeric chat id instead.
"""

from __future__ import annotations

from config.constants.buzz import (
    BUZZ_AUTH_TAG_ENV,
    BUZZ_DEFAULT_CHANNEL_ENV,
    BUZZ_PATH_ENV,
    BUZZ_PRIVATE_KEY_ENV,
    BUZZ_RELAY_URL_ENV,
)
from integrations.buzz.verifier import verify_buzz
from integrations.setup_flow import IntegrationSetupSpec, SetupField

RELAY_URL_FIELD = "relay_url"
PRIVATE_KEY_FIELD = "private_key"
DEFAULT_CHANNEL_FIELD = "default_channel"
AUTH_TAG_FIELD = "auth_tag"
BUZZ_PATH_FIELD = "buzz_path"

BUZZ_SETUP = IntegrationSetupSpec(
    service="buzz",
    fields=(
        SetupField(
            name=RELAY_URL_FIELD,
            label="Buzz relay URL",
            prompt="Buzz relay URL (e.g. https://buzz.example.com)",
            env_var=BUZZ_RELAY_URL_ENV,
            default="http://localhost:3000",
            required=False,
        ),
        SetupField(
            name=PRIVATE_KEY_FIELD,
            label="Buzz agent private key",
            prompt="Buzz agent Nostr private key (hex or nsec1..., from `buzz-admin generate-key`)",
            env_var=BUZZ_PRIVATE_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=DEFAULT_CHANNEL_FIELD,
            label="Default channel",
            prompt="Default channel UUID (see `buzz channels list`, optional)",
            env_var=BUZZ_DEFAULT_CHANNEL_ENV,
            required=False,
        ),
        SetupField(
            name=AUTH_TAG_FIELD,
            label="Buzz auth tag",
            prompt="Buzz NIP-OA auth tag JSON (optional, for owner attestation)",
            env_var=BUZZ_AUTH_TAG_ENV,
            required=False,
            secret=True,
        ),
        SetupField(
            name=BUZZ_PATH_FIELD,
            label="Buzz CLI binary path",
            prompt="buzz-cli binary path or name",
            env_var=BUZZ_PATH_ENV,
            default="buzz",
            required=False,
        ),
    ),
    verify=verify_buzz,
)

__all__ = [
    "AUTH_TAG_FIELD",
    "BUZZ_PATH_FIELD",
    "BUZZ_SETUP",
    "DEFAULT_CHANNEL_FIELD",
    "PRIVATE_KEY_FIELD",
    "RELAY_URL_FIELD",
]
