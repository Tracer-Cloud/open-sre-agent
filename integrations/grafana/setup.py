"""What Grafana needs before it is considered configured.

``verify_ssl`` used to be a wizard confirm (and was dropped entirely by the CLI
handler). Treating it as a defaulted text field, with an optional CA bundle,
keeps both surfaces on the same credentials without a branching prompt.
"""

from __future__ import annotations

from config.constants.grafana import (
    GRAFANA_CA_BUNDLE_ENV,
    GRAFANA_INSTANCE_URL_ENV,
    GRAFANA_READ_TOKEN_ENV,
    GRAFANA_VERIFY_SSL_ENV,
)
from integrations.grafana.verifier import verify_grafana
from integrations.setup_flow import IntegrationSetupSpec, SetupField

ENDPOINT_FIELD = "endpoint"
API_KEY_FIELD = "api_key"
VERIFY_SSL_FIELD = "verify_ssl"
CA_BUNDLE_FIELD = "ca_bundle"

GRAFANA_SETUP = IntegrationSetupSpec(
    service="grafana",
    fields=(
        SetupField(
            name=ENDPOINT_FIELD,
            label="Grafana instance URL",
            prompt="Instance URL (e.g. https://myorg.grafana.net)",
            env_var=GRAFANA_INSTANCE_URL_ENV,
        ),
        SetupField(
            name=API_KEY_FIELD,
            label="Service account token",
            prompt="Service account token",
            env_var=GRAFANA_READ_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=VERIFY_SSL_FIELD,
            label="Verify SSL certificate",
            prompt="Verify SSL certificate (true or false)",
            env_var=GRAFANA_VERIFY_SSL_ENV,
            default="true",
        ),
        SetupField(
            name=CA_BUNDLE_FIELD,
            label="CA bundle path",
            prompt="Path to CA bundle for SSL verification (leave blank for system defaults)",
            env_var=GRAFANA_CA_BUNDLE_ENV,
            required=False,
        ),
    ),
    verify=verify_grafana,
)

__all__ = [
    "API_KEY_FIELD",
    "CA_BUNDLE_FIELD",
    "ENDPOINT_FIELD",
    "GRAFANA_SETUP",
    "VERIFY_SSL_FIELD",
]
