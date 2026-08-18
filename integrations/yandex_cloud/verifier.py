"""Yandex Cloud verifier — mints a token and reads the configured folder.

Registered with the central plugin registry at import time; the loader in
``integrations/_verifiers_loader.py`` is what imports this module.

The either/or rule across credential fields lives on the config model this
validates against, which is what makes setup and health checks agree no matter
which surface collected the values.
"""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from integrations.yandex_cloud.config import YandexCloudIntegrationConfig
from integrations.yandex_cloud.rest_client import YandexCloudClient

verify_yandex_cloud = register_probe_verifier(
    "yandex_cloud",
    config=YandexCloudIntegrationConfig.model_validate,
    client=YandexCloudClient,
)

__all__ = ["verify_yandex_cloud"]
