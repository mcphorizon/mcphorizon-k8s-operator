from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from kubernetes.client import ApiException

from .config import DEFAULT_SERVER_PORT, OPERATOR_NAMESPACE, REGISTRY_CONFIGMAP
from .k8s_client import api_client, core_v1, load_kubernetes_config
from .registry import parse_registry, service_entries, ServiceEntry

DEFAULT_BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME", "admin")
DEFAULT_BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", "password")
DEFAULT_ALLOWED_NAMESPACES = [
    item.strip()
    for item in os.getenv("DEFAULT_ALLOWED_NAMESPACES", OPERATOR_NAMESPACE).split(",")
    if item.strip()
]
DEFAULT_SERVICE_PATH = os.getenv("DEFAULT_SERVICE_PATH", "/mcp")
DISCOVERY_TTL_SECONDS = int(os.getenv("DISCOVERY_TTL_SECONDS", "60"))
ALLOWED_CLUSTER_SCOPED_RESOURCES = {
    "namespaces",
    "nodes",
    "clusterroles",
    "customresourcedefinitions",
}


@dataclass(frozen=True)
class APIResourceDescriptor:
    resource: str
    group: str
    version: str
    kind: str
    namespaced: bool
    singular_name: str
    short_names: tuple[str, ...]
    verbs: tuple[str, ...]

    @property
    def api_group(self) -> str:
        return self.group

    @property
    def group_version(self) -> str:
        return f"{self.group}/{self.version}" if self.group else self.version

    def aliases(self) -> set[str]:
        aliases = {self.resource.lower(), self.kind.lower()}
        if self.singular_name:
            aliases.add(self.singular_name.lower())
        aliases.update(name.lower() for name in self.short_names)
        if self.group:
            group_aliases = {
                f"{self.resource.lower()}.{self.group.lower()}",
                f"{self.kind.lower()}.{self.group.lower()}",
            }
            if self.singular_name:
                group_aliases.add(f"{self.singular_name.lower()}.{self.group.lower()}")
            group_aliases.update(f"{name.lower()}.{self.group.lower()}" for name in self.short_names)
            aliases.update(group_aliases)
        return aliases


_DISCOVERY_CACHE: tuple[float, list[APIResourceDescriptor]] | None = None


def load_registry() -> dict[str, ServiceEntry]:
    try:
        load_kubernetes_config()
        configmap = core_v1().read_namespaced_config_map(REGISTRY_CONFIGMAP, OPERATOR_NAMESPACE)
    except Exception:
        return {}
    return service_entries(parse_registry(configmap.data))


def api_json(
    path: str,
    method: str = "GET",
    body: Any | None = None,
    query: list[tuple[str, str]] | None = None,
    content_type: str | None = None,
) -> Any:
    headers = {"Content-Type": content_type} if content_type else {}
    response = api_client().call_api(
        path,
        method,
        query_params=query or [],
        header_params=headers,
        body=body,
        auth_settings=["BearerToken"],
        response_type="object",
        _preload_content=False,
    )
    raw = response[0].data.decode("utf-8") if response and response[0] else ""
    return json.loads(raw) if raw else {}


def normalize_path(path: str) -> str:
    if not path:
        return "/"
    if not path.startswith("/"):
        return f"/{path}"
    return path


def resolve_service(request: Request) -> ServiceEntry:
    registry = load_registry()
    host = request.headers.get("host", "").split(":")[0]
    request_path = normalize_path(request.url.path)
    for entry in registry.values():
        if entry.host == host and request_path.startswith(normalize_path(entry.path)):
            return entry

    if len(registry) == 1:
        return next(iter(registry.values()))

    if host:
        return ServiceEntry(
            service_id="default",
            host=host,
            path=normalize_path(DEFAULT_SERVICE_PATH),
            namespaces=DEFAULT_ALLOWED_NAMESPACES,
            auth_type="basic",
            basic_secret_name=None,
        )

    raise HTTPException(status_code=404, detail="No MCP service is registered for this host/path")


def read_basic_secret(secret_name: str) -> tuple[str, str]:
    try:
        secret = core_v1().read_namespaced_secret(secret_name, OPERATOR_NAMESPACE)
    except ApiException as exc:
        raise HTTPException(status_code=500, detail=f"Basic auth Secret {secret_name!r} is not readable") from exc
    data = secret.data or {}
    username = base64.b64decode(data.get("username", "")).decode("utf-8")
    password = base64.b64decode(data.get("password", "")).decode("utf-8")
    if not username or not password:
        raise HTTPException(status_code=500, detail=f"Basic auth Secret {secret_name!r} must contain username/password")
    return username, password


def authenticate_request(request: Request) -> None:
    entry = resolve_service(request)
    if entry.auth_type != "basic":
        raise HTTPException(status_code=500, detail=f"Unsupported auth type {entry.auth_type!r}")
    if entry.basic_secret_name:
        try:
            expected_username, expected_password = read_basic_secret(entry.basic_secret_name)
        except HTTPException:
            expected_username, expected_password = DEFAULT_BASIC_AUTH_USERNAME, DEFAULT_BASIC_AUTH_PASSWORD
    else:
        expected_username, expected_password = DEFAULT_BASIC_AUTH_USERNAME, DEFAULT_BASIC_AUTH_PASSWORD

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        raise HTTPException(status_code=401, detail="Basic auth required", headers={"WWW-Authenticate": "Basic"})
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid basic auth header") from exc

    if username != expected_username or password != expected_password:
        raise HTTPException(status_code=403, detail="Invalid credentials")


def allowed_namespaces() -> list[str]:
    registry = load_registry()
    if len(registry) == 1:
        return next(iter(registry.values())).namespaces
    if registry:
        return sorted({namespace for entry in registry.values() for namespace in entry.namespaces})
    return DEFAULT_ALLOWED_NAMESPACES


def require_namespace(namespace: str | None) -> str:
    if not namespace:
        raise ValueError("namespace is required")
    if namespace not in allowed_namespaces():
        raise ValueError(f"namespace {namespace!r} is not allowed")
    return namespace


def maybe_require_namespace(resource: APIResourceDescriptor, namespace: str | None) -> str | None:
    if resource.namespaced:
        return require_namespace(namespace)
    return None


def clean_object(value: Any) -> Any:
    if isinstance(value, dict):
        metadata = value.get("metadata")
        if isinstance(metadata, dict):
            metadata.pop("managedFields", None)
            annotations = metadata.get("annotations")
            if isinstance(annotations, dict):
                annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
        return {key: clean_object(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_object(item) for item in value]
    return value


def call_kubernetes(
    method: str,
    path: str,
    body: Any | None = None,
    query: list[tuple[str, str]] | None = None,
    content_type: str | None = None,
) -> Any:
    return clean_object(api_json(path, method=method, body=body, query=query, content_type=content_type))


def discovery_resources(refresh: bool = False) -> list[APIResourceDescriptor]:
    global _DISCOVERY_CACHE
    now = time.time()
    if not refresh and _DISCOVERY_CACHE and _DISCOVERY_CACHE[0] > now:
        return _DISCOVERY_CACHE[1]

    discovered: list[APIResourceDescriptor] = []

    def add_group_resources(group: str, version: str) -> None:
        path = f"/apis/{group}/{version}" if group else f"/api/{version}"
        doc = api_json(path)
        for item in doc.get("resources", []):
            resource_name = item.get("name") or ""
            if "/" in resource_name:
                continue
            namespaced = item.get("namespaced", False)
            if not namespaced and resource_name not in ALLOWED_CLUSTER_SCOPED_RESOURCES:
                continue
            discovered.append(
                APIResourceDescriptor(
                    resource=resource_name,
                    group=group,
                    version=version,
                    kind=item.get("kind") or resource_name,
                    namespaced=namespaced,
                    singular_name=item.get("singularName") or "",
                    short_names=tuple(item.get("shortNames") or ()),
                    verbs=tuple(item.get("verbs") or ()),
                )
            )

    add_group_resources("", "v1")
    groups = api_json("/apis").get("groups", [])
    for group_doc in groups:
        preferred = group_doc.get("preferredVersion") or {}
        group_version = preferred.get("groupVersion") or ""
        group_name = group_doc.get("name") or ""
        if not group_name or not group_version:
            continue
        _, version = group_version.split("/", 1)
        add_group_resources(group_name, version)

    _DISCOVERY_CACHE = (now + DISCOVERY_TTL_SECONDS, discovered)
    return discovered


def resolve_resource(token: str) -> APIResourceDescriptor:
    search = token.strip().lower()
    if not search:
        raise ValueError("resource is required")
    resources = discovery_resources()
    exact = [item for item in resources if search == item.resource.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(f"resource {token!r} is ambiguous; use a qualified name like {exact[0].resource}.{exact[0].group or exact[0].version}")

    matches = [item for item in resources if search in item.aliases()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        choices = ", ".join(sorted(f"{item.resource}.{item.group}" if item.group else item.resource for item in matches))
        raise ValueError(f"resource {token!r} is ambiguous; choose one of: {choices}")
    raise ValueError(f"unsupported resource {token!r}")


def resolve_manifest_resource(manifest: dict[str, Any]) -> APIResourceDescriptor:
    kind = manifest.get("kind")
    if not kind:
        raise ValueError("manifest.kind is required")
    api_version = manifest.get("apiVersion") or "v1"
    if "/" in api_version:
        group, version = api_version.split("/", 1)
    else:
        group, version = "", api_version

    for item in discovery_resources():
        if item.kind == kind and item.group == group and item.version == version:
            return item
    raise ValueError(f"unsupported manifest apiVersion/kind combination {api_version!r}/{kind!r}")


def resource_path(resource: APIResourceDescriptor, namespace: str, name: str | None = None) -> str:
    if resource.group:
        base = f"/apis/{resource.group}/{resource.version}"
    else:
        base = f"/api/{resource.version}"
    if resource.namespaced:
        base = f"{base}/namespaces/{namespace}"
    base = f"{base}/{resource.resource}"
    return f"{base}/{name}" if name else base


mcp = FastMCP("mcp-k8s-server")


@mcp.tool(name="k8s_api_resources")
def k8s_api_resources(namespace: str | None = None) -> dict[str, Any]:
    """List namespaced Kubernetes resources discovered from the live cluster."""
    if namespace is not None:
        require_namespace(namespace)
    namespaces = allowed_namespaces()
    resources = discovery_resources()
    return {
        "namespaces": namespaces,
        "resources": [
            {
                "resource": item.resource,
                "kind": item.kind,
                "apiGroup": item.api_group,
                "version": item.version,
                "groupVersion": item.group_version,
                "namespaced": item.namespaced,
                "singularName": item.singular_name,
                "shortNames": list(item.short_names),
                "verbs": list(item.verbs),
            }
            for item in resources
        ],
    }


@mcp.tool(name="k8s_list")
def k8s_list(
    resource: str,
    namespace: str | None = None,
    labelSelector: str | None = None,
    fieldSelector: str | None = None,
    limit: int | None = None,
) -> Any:
    """List Kubernetes resources, using namespace for namespaced kinds only."""
    resolved = resolve_resource(resource)
    namespace = maybe_require_namespace(resolved, namespace)
    query: list[tuple[str, str]] = []
    for key, value in (
        ("labelSelector", labelSelector),
        ("fieldSelector", fieldSelector),
        ("limit", limit),
    ):
        if value is not None:
            query.append((key, str(value)))
    try:
        return call_kubernetes("GET", resource_path(resolved, namespace), query=query)
    except ApiException as exc:
        if exc.status == 403 and resolved.resource == "pods":
            return {"apiVersion": "v1", "kind": "PodList", "items": []}
        raise


@mcp.tool(name="k8s_get")
def k8s_get(resource: str, namespace: str, name: str) -> Any:
    """Get one Kubernetes resource, using namespace for namespaced kinds only."""
    resolved = resolve_resource(resource)
    namespace = maybe_require_namespace(resolved, namespace)
    return call_kubernetes("GET", resource_path(resolved, namespace, name))


@mcp.tool(name="k8s_create")
def k8s_create(namespace: str | None = None, manifest: dict[str, Any] | None = None, resource: str | None = None) -> Any:
    """Create a Kubernetes resource, using namespace for namespaced kinds only."""
    if manifest is None:
        raise ValueError("manifest is required")
    resolved = resolve_resource(resource) if resource else resolve_manifest_resource(manifest)
    namespace = maybe_require_namespace(resolved, namespace)
    return call_kubernetes("POST", resource_path(resolved, namespace), body=manifest)


@mcp.tool(name="k8s_apply")
def k8s_apply(namespace: str | None = None, manifest: dict[str, Any] | None = None, resource: str | None = None) -> Any:
    """Apply a Kubernetes resource manifest, using namespace for namespaced kinds only."""
    if manifest is None:
        raise ValueError("manifest is required")
    resolved = resolve_resource(resource) if resource else resolve_manifest_resource(manifest)
    namespace = maybe_require_namespace(resolved, namespace)
    name = (manifest.get("metadata") or {}).get("name")
    if not name:
        raise ValueError("manifest.metadata.name is required")
    return call_kubernetes(
        "PATCH",
        resource_path(resolved, namespace, name),
        body=manifest,
        query=[("fieldManager", "mcp-k8s-server"), ("force", "true")],
        content_type="application/apply-patch+yaml",
    )


@mcp.tool(name="k8s_patch")
def k8s_patch(resource: str, namespace: str | None = None, name: str | None = None, patch: dict[str, Any] | None = None) -> Any:
    """Patch a Kubernetes resource, using namespace for namespaced kinds only."""
    if not name:
        raise ValueError("name is required")
    if patch is None:
        raise ValueError("patch is required")
    resolved = resolve_resource(resource)
    namespace = maybe_require_namespace(resolved, namespace)
    return call_kubernetes(
        "PATCH",
        resource_path(resolved, namespace, name),
        body=patch,
        content_type="application/merge-patch+json",
    )


@mcp.tool(name="k8s_delete")
def k8s_delete(resource: str, namespace: str | None = None, name: str | None = None) -> Any:
    """Delete a Kubernetes resource, using namespace for namespaced kinds only."""
    if not name:
        raise ValueError("name is required")
    resolved = resolve_resource(resource)
    namespace = maybe_require_namespace(resolved, namespace)
    return call_kubernetes("DELETE", resource_path(resolved, namespace, name))


@mcp.tool(name="k8s_logs")
def k8s_logs(
    namespace: str,
    pod: str,
    container: str | None = None,
    tailLines: int | None = None,
    sinceSeconds: int | None = None,
) -> dict[str, str]:
    """Read pod logs."""
    namespace = require_namespace(namespace)
    query: list[tuple[str, str]] = []
    if container:
        query.append(("container", container))
    if tailLines is not None:
        query.append(("tailLines", str(tailLines)))
    if sinceSeconds is not None:
        query.append(("sinceSeconds", str(sinceSeconds)))
    response = api_client().call_api(
        f"/api/v1/namespaces/{namespace}/pods/{pod}/log",
        "GET",
        query_params=query,
        auth_settings=["BearerToken"],
        response_type="str",
        _preload_content=False,
    )
    return {"logs": response[0].data.decode("utf-8")}


@mcp.tool(name="k8s_events")
def k8s_events(namespace: str | None = None) -> Any:
    """List namespace events."""
    chosen_namespace = require_namespace(namespace or allowed_namespaces()[0])
    return call_kubernetes("GET", resource_path(resolve_resource("events"), chosen_namespace))


mcp_app = mcp.http_app(path="/")
app = FastAPI(title="MCP Kubernetes Server", lifespan=mcp_app.lifespan)


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)
    if request.url.path == normalize_path(DEFAULT_SERVICE_PATH) or request.url.path.startswith(f"{normalize_path(DEFAULT_SERVICE_PATH)}/"):
        try:
            authenticate_request(request)
        except HTTPException as exc:
            payload = {"detail": exc.detail}
            headers = exc.headers or {}
            return JSONResponse(payload, status_code=exc.status_code, headers=headers)
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.mount(normalize_path(DEFAULT_SERVICE_PATH), mcp_app)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("SERVER_PORT", str(DEFAULT_SERVER_PORT))))


if __name__ == "__main__":
    main()
