from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from kubernetes import client
from kubernetes.client import ApiException

from mcp_k8s_operator.k8s_client import apps_v1, core_v1, custom_objects, load_kubernetes_config, networking_v1
from .conftest import call_tool, initialize_session, tool_text


pytestmark = pytest.mark.skipif(
    os.getenv("MCP_OPERATOR_E2E") != "1",
    reason="Set MCP_OPERATOR_E2E=1 to run the operator install flow test",
)


ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ROOT / "k8s-manifests"
GROUP = "mcp.k8s.io"
VERSION = "v1alpha1"
PLURAL = "mcpkubernetesservers"
INGRESS_SUFFIX = os.getenv("MCP_INGRESS_SUFFIX", "68.220.202.177.nip.io")
OPERATOR_IMAGE = os.getenv(
    "MCP_OPERATOR_IMAGE",
    "registry.68.220.202.177.nip.io/mcpoperator-python-operator:latest",
)
SERVER_IMAGE = os.getenv(
    "MCP_SERVER_IMAGE",
    "registry.68.220.202.177.nip.io/mcpoperator-python:latest",
)


def load_manifest(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [item for item in yaml.safe_load_all(handle) if item]


def upsert_namespace(name: str) -> None:
    body = client.V1Namespace(metadata=client.V1ObjectMeta(name=name))
    try:
        core_v1().read_namespace(name)
        core_v1().patch_namespace(name, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        core_v1().create_namespace(body)


def upsert_service_account(doc: dict[str, Any]) -> None:
    metadata = doc["metadata"]
    body = client.V1ServiceAccount(metadata=client.V1ObjectMeta(**metadata))
    namespace = metadata["namespace"]
    name = metadata["name"]
    try:
        core_v1().read_namespaced_service_account(name, namespace)
        core_v1().patch_namespaced_service_account(name, namespace, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        core_v1().create_namespaced_service_account(namespace, body)


def upsert_secret(doc: dict[str, Any]) -> None:
    metadata = doc["metadata"]
    namespace = metadata["namespace"]
    name = metadata["name"]
    body = client.V1Secret(
        metadata=client.V1ObjectMeta(**metadata),
        type=doc.get("type"),
        string_data=doc.get("stringData"),
        data=doc.get("data"),
    )
    try:
        core_v1().read_namespaced_secret(name, namespace)
        core_v1().patch_namespaced_secret(name, namespace, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        core_v1().create_namespaced_secret(namespace, body)


def upsert_cluster_role(doc: dict[str, Any]) -> None:
    api = client.RbacAuthorizationV1Api()
    name = doc["metadata"]["name"]
    body = client.V1ClusterRole(
        metadata=client.V1ObjectMeta(name=name),
        rules=[client.V1PolicyRule(**rule) for rule in doc.get("rules", [])],
    )
    try:
        api.read_cluster_role(name)
        api.patch_cluster_role(name, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        api.create_cluster_role(body)


def upsert_cluster_role_binding(doc: dict[str, Any]) -> None:
    api = client.RbacAuthorizationV1Api()
    name = doc["metadata"]["name"]
    body = client.V1ClusterRoleBinding(
        metadata=client.V1ObjectMeta(name=name),
        role_ref=client.V1RoleRef(**doc["roleRef"]),
        subjects=[client.V1Subject(**subject) for subject in doc.get("subjects", [])],
    )
    try:
        api.read_cluster_role_binding(name)
        api.patch_cluster_role_binding(name, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        api.create_cluster_role_binding(body)


def upsert_deployment(doc: dict[str, Any]) -> None:
    metadata = doc["metadata"]
    namespace = metadata["namespace"]
    name = metadata["name"]
    body = client.ApiClient()._ApiClient__deserialize_model(doc, client.V1Deployment)
    try:
        apps_v1().read_namespaced_deployment(name, namespace)
        apps_v1().patch_namespaced_deployment(name, namespace, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        apps_v1().create_namespaced_deployment(namespace, body)


def upsert_crd(doc: dict[str, Any]) -> None:
    api = client.ApiextensionsV1Api()
    name = doc["metadata"]["name"]
    body = client.ApiClient()._ApiClient__deserialize_model(doc, client.V1CustomResourceDefinition)
    try:
        api.read_custom_resource_definition(name)
        api.patch_custom_resource_definition(name, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        api.create_custom_resource_definition(body)


def wait_for_deployment(namespace: str, name: str, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        deployment = apps_v1().read_namespaced_deployment(name, namespace)
        if deployment.status.ready_replicas and deployment.status.ready_replicas >= 1:
            return
        time.sleep(3)
    raise AssertionError(f"deployment {namespace}/{name} was not ready")


def wait_for_custom_resource(namespace: str, name: str, timeout: int = 300) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resource = custom_objects().get_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, name)
        status = resource.get("status") or {}
        conditions = {item["type"]: item["status"] for item in status.get("conditions") or []}
        if status.get("url") and conditions.get("Ready") == "True":
            return resource
        time.sleep(3)
    raise AssertionError(f"custom resource {namespace}/{name} did not become ready")


def wait_for_ingress_http(base_url: str, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            response = httpx.post(
                base_url,
                headers={
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "probe", "version": "1.0"},
                    },
                },
                timeout=20,
                verify=False,
                follow_redirects=True,
            )
            if response.status_code in (401, 403):
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(5)
    raise AssertionError(f"ingress {base_url} did not respond with auth challenge: {last_error}")


@pytest.fixture(scope="module")
def provisioned_service() -> dict[str, str]:
    load_kubernetes_config()

    suffix = uuid.uuid4().hex[:8]
    operator_namespace = f"mcp-e2e-{suffix}"
    target_namespace = os.getenv("MCP_ALLOWED_NAMESPACE", f"mcp-e2e-target-{suffix}")
    name = f"mcp-e2e-{suffix}"
    host = f"{name}.{INGRESS_SUFFIX}"
    base_url = f"http://{host}/mcp/"

    upsert_namespace(operator_namespace)
    upsert_namespace(target_namespace)
    upsert_crd(load_manifest(MANIFESTS / "00-crd.yaml")[0])

    for doc in load_manifest(MANIFESTS / "01-operator-rbac.yaml"):
        kind = doc["kind"]
        if kind == "ServiceAccount":
            doc["metadata"]["namespace"] = operator_namespace
            upsert_service_account(doc)
        elif kind == "ClusterRole":
            upsert_cluster_role(doc)
        elif kind == "ClusterRoleBinding":
            doc["subjects"][0]["namespace"] = operator_namespace
            upsert_cluster_role_binding(doc)

    secret = load_manifest(MANIFESTS / "02-basic-auth-secret.yaml")[0]
    secret["metadata"]["namespace"] = operator_namespace
    upsert_secret(secret)

    deployment = load_manifest(MANIFESTS / "03-operator-deployment.yaml")[0]
    deployment["metadata"]["namespace"] = operator_namespace
    deployment["spec"]["template"]["spec"]["serviceAccountName"] = "mcp-operator"
    deployment["spec"]["template"]["spec"]["containers"][0]["image"] = OPERATOR_IMAGE
    for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]:
        if item["name"] == "OPERATOR_NAMESPACE":
            item["value"] = operator_namespace
        elif item["name"] == "SERVER_IMAGE":
            item["value"] = SERVER_IMAGE
        elif item["name"] == "APP_NAME":
            item["value"] = name
    upsert_deployment(deployment)
    wait_for_deployment(operator_namespace, "mcp-operator")

    body = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "MCPKubernetesServer",
        "metadata": {"name": name, "namespace": operator_namespace},
        "spec": {
            "namespaces": [target_namespace],
            "ingress": {
                "enabled": True,
                "host": host,
                "path": "/mcp",
                "ingressClassName": os.getenv("MCP_INGRESS_CLASS", "nginx"),
                "tls": {"enabled": False},
            },
            "auth": {
                "type": "basic",
                "basic": {"secretName": "mcpoperator-basic-auth"},
            },
        },
    }
    try:
        custom_objects().get_namespaced_custom_object(GROUP, VERSION, operator_namespace, PLURAL, name)
        custom_objects().patch_namespaced_custom_object(GROUP, VERSION, operator_namespace, PLURAL, name, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        custom_objects().create_namespaced_custom_object(GROUP, VERSION, operator_namespace, PLURAL, body)

    resource = wait_for_custom_resource(operator_namespace, name)
    wait_for_ingress_http(base_url)

    return {
        "base_url": base_url,
        "operator_namespace": operator_namespace,
        "target_namespace": target_namespace,
        "name": name,
        "host": host,
        "status_url": resource["status"]["url"],
    }


def test_operator_creates_cr_backed_ingress(provisioned_service: dict[str, str]) -> None:
    ingress = networking_v1().read_namespaced_ingress(
        f"mcp-{provisioned_service['name']}",
        provisioned_service["operator_namespace"],
    )
    assert ingress.spec.rules[0].host == provisioned_service["host"]
    assert ingress.spec.rules[0].http.paths[0].path == "/mcp"


def test_cr_ingress_allows_mcp_operations(provisioned_service: dict[str, str]) -> None:
    session_id, payload = initialize_session(provisioned_service["base_url"])
    assert payload["result"]["serverInfo"]["name"] == "mcp-k8s-server"

    resources = tool_text(call_tool(provisioned_service["base_url"], session_id, "k8s_api_resources", {}))
    assert provisioned_service["target_namespace"] in resources["namespaces"]

    pods = tool_text(
        call_tool(
            provisioned_service["base_url"],
            session_id,
            "k8s_list",
            {"resource": "pods", "namespace": provisioned_service["target_namespace"]},
        )
    )
    assert pods["kind"] == "PodList"
    assert "items" in pods

    forbidden = call_tool(
        provisioned_service["base_url"],
        session_id,
        "k8s_list",
        {"resource": "pods", "namespace": provisioned_service["operator_namespace"]},
    )
    assert forbidden["error"]["code"] == -32602
    assert "not allowed" in forbidden["error"]["message"]
