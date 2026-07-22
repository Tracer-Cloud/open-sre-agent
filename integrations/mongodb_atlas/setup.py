"""What MongoDB Atlas needs before it is considered configured."""

from __future__ import annotations

from config.constants.mongodb_atlas import (
    MONGODB_ATLAS_BASE_URL_ENV,
    MONGODB_ATLAS_PRIVATE_KEY_ENV,
    MONGODB_ATLAS_PROJECT_ID_ENV,
    MONGODB_ATLAS_PUBLIC_KEY_ENV,
)
from integrations.mongodb_atlas import DEFAULT_ATLAS_BASE_URL
from integrations.mongodb_atlas.verifier import verify_mongodb_atlas
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_PUBLIC_KEY_FIELD = "api_public_key"
API_PRIVATE_KEY_FIELD = "api_private_key"
PROJECT_ID_FIELD = "project_id"
BASE_URL_FIELD = "base_url"

MONGODB_ATLAS_SETUP = IntegrationSetupSpec(
    service="mongodb_atlas",
    fields=(
        SetupField(
            name=API_PUBLIC_KEY_FIELD,
            label="Atlas API public key",
            env_var=MONGODB_ATLAS_PUBLIC_KEY_ENV,
        ),
        SetupField(
            name=API_PRIVATE_KEY_FIELD,
            label="Atlas API private key",
            env_var=MONGODB_ATLAS_PRIVATE_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=PROJECT_ID_FIELD,
            label="Atlas project ID",
            prompt="Atlas project ID (group ID)",
            env_var=MONGODB_ATLAS_PROJECT_ID_ENV,
        ),
        SetupField(
            name=BASE_URL_FIELD,
            label="Atlas API base URL",
            env_var=MONGODB_ATLAS_BASE_URL_ENV,
            default=DEFAULT_ATLAS_BASE_URL,
        ),
    ),
    verify=verify_mongodb_atlas,
)

__all__ = [
    "API_PRIVATE_KEY_FIELD",
    "API_PUBLIC_KEY_FIELD",
    "BASE_URL_FIELD",
    "MONGODB_ATLAS_SETUP",
    "PROJECT_ID_FIELD",
]
