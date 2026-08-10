"""What Google Cloud needs before it is considered configured.

The three auth paths are a picker rather than four flat prompts: on GKE with
Workload Identity the correct answer is "fill nothing in", and a flat form that
asks for a service-account key next to an impersonation target invites the
operator to supply both.
"""

from __future__ import annotations

from config.constants.gcp import (
    GCP_ADDITIONAL_PROJECTS_ENV,
    GCP_IMPERSONATE_SERVICE_ACCOUNT_ENV,
    GCP_PROJECT_ID_ENV,
    GCP_SERVICE_ACCOUNT_KEY_ENV,
)
from integrations.gcp.verifier import verify_gcp
from integrations.setup_flow import IntegrationSetupSpec, SetupField, SetupMode

PROJECT_ID_FIELD = "project_id"
ADDITIONAL_PROJECTS_FIELD = "additional_projects"
SERVICE_ACCOUNT_KEY_FIELD = "service_account_key"
IMPERSONATE_FIELD = "impersonate_service_account"

ADC_MODE = "adc"
SERVICE_ACCOUNT_MODE = "service_account"
IMPERSONATION_MODE = "impersonation"

GCP_SETUP = IntegrationSetupSpec(
    service="gcp",
    mode_prompt="How should OpenSRE authenticate to Google Cloud?",
    modes=(
        SetupMode(
            value=ADC_MODE,
            label="Application Default Credentials (GKE Workload Identity, gcloud login)",
        ),
        SetupMode(
            value=SERVICE_ACCOUNT_MODE,
            label="Service account key (JSON file path or literal JSON)",
            fields=(SERVICE_ACCOUNT_KEY_FIELD,),
        ),
        SetupMode(
            value=IMPERSONATION_MODE,
            label="Impersonate a service account from the ambient credential",
            fields=(IMPERSONATE_FIELD,),
        ),
    ),
    fields=(
        SetupField(
            name=PROJECT_ID_FIELD,
            label="GCP project id",
            prompt="Default GCP project id (e.g. acme-prod)",
            env_var=GCP_PROJECT_ID_ENV,
        ),
        SetupField(
            name=ADDITIONAL_PROJECTS_FIELD,
            label="Additional projects",
            prompt=(
                "Other project ids this credential can read, comma-separated "
                "(leave empty for just the default project)"
            ),
            env_var=GCP_ADDITIONAL_PROJECTS_ENV,
            required=False,
        ),
        SetupField(
            name=SERVICE_ACCOUNT_KEY_FIELD,
            label="Service account key",
            prompt="Path to the service-account JSON key file, or the JSON itself",
            env_var=GCP_SERVICE_ACCOUNT_KEY_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=IMPERSONATE_FIELD,
            label="Service account to impersonate",
            prompt=(
                "Service account email to impersonate "
                "(e.g. sre-ro@acme-prod.iam.gserviceaccount.com)"
            ),
            env_var=GCP_IMPERSONATE_SERVICE_ACCOUNT_ENV,
            required=False,
        ),
    ),
    verify=verify_gcp,
)

__all__ = [
    "ADC_MODE",
    "ADDITIONAL_PROJECTS_FIELD",
    "GCP_SETUP",
    "IMPERSONATE_FIELD",
    "IMPERSONATION_MODE",
    "PROJECT_ID_FIELD",
    "SERVICE_ACCOUNT_KEY_FIELD",
    "SERVICE_ACCOUNT_MODE",
]
