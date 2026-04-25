from __future__ import annotations

import os


def env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


OPERATOR_NAMESPACE = env("OPERATOR_NAMESPACE", "automationx")
APP_NAME = env("APP_NAME", "mcpoperator")
SERVER_IMAGE = env(
    "SERVER_IMAGE",
    "registry.68.220.202.177.nip.io/mcpoperator-python:latest",
)
OPERATOR_IMAGE = env(
    "OPERATOR_IMAGE",
    "registry.68.220.202.177.nip.io/mcpoperator-python-operator:latest",
)
SERVER_DEPLOYMENT = env("SERVER_DEPLOYMENT", "mcp-server")
SERVER_SERVICE = env("SERVER_SERVICE", "mcp-server")
SERVER_SERVICE_ACCOUNT = env("SERVER_SERVICE_ACCOUNT", "mcp-server")
REGISTRY_CONFIGMAP = env("REGISTRY_CONFIGMAP", "mcp-server-registry")
REGISTRY_KEY = env("REGISTRY_KEY", "registry.yaml")
DEFAULT_INGRESS_CLASS = env("DEFAULT_INGRESS_CLASS", "nginx")
DEFAULT_CLUSTER_ISSUER = env("DEFAULT_CLUSTER_ISSUER", "letsencrypt-prod")
DEFAULT_SERVER_PORT = int(env("SERVER_PORT", "8080"))
