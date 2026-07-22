"""What Helm needs before it is considered configured.

Every field here is optional except ``helm_path``, which itself defaults to
``helm`` on ``PATH`` — so a bare enter through every prompt still produces a
usable config. Note that the *env-only* discovery path
(``integrations._catalog_impl.load_env_integrations``) additionally gates on
``OSRE_HELM_INTEGRATION`` being truthy; that flag is a separate, deliberate
opt-in (Helm shells out to a local binary) and is not part of this spec.
"""

from __future__ import annotations

from config.constants.helm import (
    HELM_KUBE_CONTEXT_ENV,
    HELM_KUBECONFIG_ENV,
    HELM_NAMESPACE_ENV,
    HELM_PATH_ENV,
)
from integrations.helm.verifier import verify_helm
from integrations.setup_flow import IntegrationSetupSpec, SetupField

HELM_PATH_FIELD = "helm_path"
KUBE_CONTEXT_FIELD = "kube_context"
KUBECONFIG_FIELD = "kubeconfig"
DEFAULT_NAMESPACE_FIELD = "default_namespace"

HELM_SETUP = IntegrationSetupSpec(
    service="helm",
    fields=(
        SetupField(
            name=HELM_PATH_FIELD,
            label="Helm binary path",
            prompt="Helm binary path or name",
            env_var=HELM_PATH_ENV,
            default="helm",
        ),
        SetupField(
            name=KUBE_CONTEXT_FIELD,
            label="Kubernetes context",
            prompt="Kubernetes context (optional, passed as --kube-context)",
            env_var=HELM_KUBE_CONTEXT_ENV,
            required=False,
        ),
        SetupField(
            name=KUBECONFIG_FIELD,
            label="Kubeconfig file path",
            prompt="Kubeconfig file path (optional, passed as --kubeconfig)",
            env_var=HELM_KUBECONFIG_ENV,
            required=False,
        ),
        SetupField(
            name=DEFAULT_NAMESPACE_FIELD,
            label="Default namespace",
            prompt="Default namespace when alerts do not specify one (optional)",
            env_var=HELM_NAMESPACE_ENV,
            required=False,
        ),
    ),
    verify=verify_helm,
)

__all__ = [
    "DEFAULT_NAMESPACE_FIELD",
    "HELM_PATH_FIELD",
    "HELM_SETUP",
    "KUBECONFIG_FIELD",
    "KUBE_CONTEXT_FIELD",
]
