"""Configuration model for the Yandex Cloud integration.

Lives in the vendor package rather than ``integrations/config_models.py``: that
module is already 1200+ lines, and the per-package layout is what the newer
integrations (grafana, honeycomb, posthog, airflow) follow.
"""

from __future__ import annotations

from pydantic import field_validator, model_validator

from config.constants.yandex_cloud import (
    AUTH_MODE_IAM_TOKEN,
    AUTH_MODE_METADATA,
    AUTH_MODE_OAUTH,
    AUTH_MODE_SA_KEY,
    AUTH_MODE_SA_KEY_FILE,
)
from config.strict_config import StrictConfigModel
from integrations._validators import normalize_str


class YandexCloudIntegrationConfig(StrictConfigModel):
    """Credentials and scope for reading a Yandex Cloud folder.

    Every read is folder-scoped — the Monitoring API rejects cross-folder
    queries outright — so ``folder_id`` is what makes the integration usable at
    all. ``cloud_id`` is only needed by the few APIs that list folders.
    """

    folder_id: str = ""
    cloud_id: str = ""
    #: Path to a service-account authorized key JSON file.
    sa_key_file: str = ""
    #: The same key inline, for deployments with no writable filesystem.
    sa_key: str = ""
    #: OAuth token for a user account. Convenient locally, discouraged for a service.
    oauth_token: str = ""
    #: A ready IAM token. Expires within 12 hours, so mostly useful for testing.
    iam_token: str = ""
    #: Set when the agent runs on a Yandex Cloud VM or function with an attached
    #: service account, where the metadata service issues tokens directly.
    use_metadata: bool = False
    integration_id: str = ""

    # Every string field, not just the two that are always filled in: the local
    # store writes an unused credential as ``null`` rather than omitting it, so
    # a config that came from `integrations setup` arrives with four Nones where
    # the env path has nothing at all. Without this the strict model rejects it
    # and the integration reads as "saved but unusable".
    _normalize_strs = field_validator(
        "folder_id",
        "cloud_id",
        "sa_key_file",
        "sa_key",
        "oauth_token",
        "iam_token",
        "integration_id",
        mode="before",
    )(normalize_str())

    @field_validator("use_metadata", mode="before")
    @classmethod
    def _normalize_use_metadata(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @property
    def auth_mode(self) -> str | None:
        """Return which credential to use, or None when none was supplied."""
        if self.sa_key_file:
            return AUTH_MODE_SA_KEY_FILE
        if self.sa_key:
            return AUTH_MODE_SA_KEY
        if self.oauth_token:
            return AUTH_MODE_OAUTH
        if self.iam_token:
            return AUTH_MODE_IAM_TOKEN
        if self.use_metadata:
            return AUTH_MODE_METADATA
        return None

    @property
    def folder_id_is_discoverable(self) -> bool:
        """Whether the folder can be read from the instance metadata service.

        An instance knows which folder it runs in, so a deployment inside
        Yandex Cloud does not have to be told.
        """
        return self.auth_mode == AUTH_MODE_METADATA

    @property
    def is_configured(self) -> bool:
        if self.auth_mode is None:
            return False
        return bool(self.folder_id) or self.folder_id_is_discoverable

    @model_validator(mode="after")
    def _require_folder_and_one_credential(self) -> YandexCloudIntegrationConfig:
        if self.auth_mode is None:
            raise ValueError(
                "Yandex Cloud needs one credential: a service-account key "
                "(sa_key_file or sa_key), an OAuth token, an IAM token, or "
                "use_metadata when running on a Yandex Cloud VM."
            )
        if not self.folder_id and not self.folder_id_is_discoverable:
            raise ValueError(
                "Yandex Cloud needs a folder_id — every read is folder-scoped. "
                "Find it in the console URL, or run `yc config get folder-id`. "
                "On a Yandex Cloud VM set use_metadata instead and the folder is "
                "read from the instance."
            )
        return self


__all__ = ["YandexCloudIntegrationConfig"]
