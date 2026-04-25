from __future__ import annotations

import kopf
from kubernetes import client
from kubernetes.client import ApiException

from .config import (
    APP_NAME,
    DEFAULT_CLUSTER_ISSUER,
    DEFAULT_INGRESS_CLASS,
    DEFAULT_SERVER_PORT,
    OPERATOR_NAMESPACE,
    REGISTRY_CONFIGMAP,
    SERVER_DEPLOYMENT,
    SERVER_IMAGE,
    SERVER_SERVICE,
    SERVER_SERVICE_ACCOUNT,
)
from .k8s_client import apps_v1, core_v1, networking_v1, rbac_v1
from .registry import dump_registry, empty_registry, parse_registry, ServiceEntry

GROUP = "mcp.k8s.io"
VERSION = "v1alpha1"
PLURAL = "mcpkubernetesservers"


def labels(server_name: str | None = None, server_namespace: str | None = None) -> dict[str, str]:
    result = {
        "app.kubernetes.io/name": "mcp-kubernetes-operator",
        "app.kubernetes.io/component": "mcp-server",
    }
    if server_name:
        result["mcp.k8s.io/server-name"] = server_name
    if server_namespace:
        result["mcp.k8s.io/server-namespace"] = server_namespace
    return result


def service_id(namespace: str, name: str) -> str:
    return name if namespace == OPERATOR_NAMESPACE else f"{namespace}-{name}"


def full_access_role_name(name: str) -> str:
    return f"mcp-{name}-full-access"


def role_binding_name(name: str) -> str:
    return f"mcp-{name}-server-binding"


def ingress_name(name: str) -> str:
    return f"mcp-{name}"


def ignore_exists(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ApiException as exc:
        if exc.status != 409:
            raise
        return None


def ensure_service_account(namespace: str) -> None:
    body = client.V1ServiceAccount(
        metadata=client.V1ObjectMeta(name=SERVER_SERVICE_ACCOUNT, labels=labels())
    )
    try:
        core_v1().read_namespaced_service_account(SERVER_SERVICE_ACCOUNT, namespace)
        core_v1().patch_namespaced_service_account(SERVER_SERVICE_ACCOUNT, namespace, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        core_v1().create_namespaced_service_account(namespace, body)


def ensure_shared_deployment(namespace: str) -> None:
    body = client.V1Deployment(
        metadata=client.V1ObjectMeta(name=SERVER_DEPLOYMENT, labels=labels()),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(match_labels={"app": SERVER_DEPLOYMENT}),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"app": SERVER_DEPLOYMENT, **labels()}),
                spec=client.V1PodSpec(
                    service_account_name=SERVER_SERVICE_ACCOUNT,
                    containers=[
                        client.V1Container(
                            name="server",
                            image=SERVER_IMAGE,
                            image_pull_policy="Always",
                            ports=[client.V1ContainerPort(container_port=DEFAULT_SERVER_PORT)],
                            env=[
                                client.V1EnvVar(name="OPERATOR_NAMESPACE", value=namespace),
                                client.V1EnvVar(name="REGISTRY_CONFIGMAP", value=REGISTRY_CONFIGMAP),
                            ],
                            readiness_probe=client.V1Probe(
                                http_get=client.V1HTTPGetAction(path="/healthz", port=DEFAULT_SERVER_PORT),
                                initial_delay_seconds=3,
                                period_seconds=5,
                            ),
                            liveness_probe=client.V1Probe(
                                http_get=client.V1HTTPGetAction(path="/healthz", port=DEFAULT_SERVER_PORT),
                                initial_delay_seconds=10,
                                period_seconds=10,
                            ),
                        )
                    ],
                ),
            ),
        ),
    )
    try:
        apps_v1().read_namespaced_deployment(SERVER_DEPLOYMENT, namespace)
        apps_v1().patch_namespaced_deployment(SERVER_DEPLOYMENT, namespace, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        apps_v1().create_namespaced_deployment(namespace, body)


def ensure_shared_service(namespace: str) -> None:
    body = client.V1Service(
        metadata=client.V1ObjectMeta(name=SERVER_SERVICE, labels=labels()),
        spec=client.V1ServiceSpec(
            type="ClusterIP",
            selector={"app": SERVER_DEPLOYMENT},
            ports=[
                client.V1ServicePort(
                    name="http",
                    port=DEFAULT_SERVER_PORT,
                    target_port=DEFAULT_SERVER_PORT,
                )
            ],
        ),
    )
    try:
        core_v1().read_namespaced_service(SERVER_SERVICE, namespace)
        core_v1().patch_namespaced_service(SERVER_SERVICE, namespace, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        core_v1().create_namespaced_service(namespace, body)


def ensure_registry_configmap(namespace: str) -> dict:
    body = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(name=REGISTRY_CONFIGMAP, labels=labels()),
        data=dump_registry(empty_registry()),
    )
    try:
        current = core_v1().read_namespaced_config_map(REGISTRY_CONFIGMAP, namespace)
        return parse_registry(current.data)
    except ApiException as exc:
        if exc.status != 404:
            raise
        core_v1().create_namespaced_config_map(namespace, body)
        return empty_registry()


def update_registry(namespace: str, entry: ServiceEntry) -> None:
    registry = ensure_registry_configmap(namespace)
    registry.setdefault("services", {})[entry.service_id] = entry.to_dict()
    body = client.V1ConfigMap(data=dump_registry(registry))
    core_v1().patch_namespaced_config_map(REGISTRY_CONFIGMAP, namespace, body)


def remove_registry_entry(namespace: str, entry_id: str) -> None:
    try:
        current = core_v1().read_namespaced_config_map(REGISTRY_CONFIGMAP, namespace)
    except ApiException as exc:
        if exc.status == 404:
            return
        raise
    registry = parse_registry(current.data)
    registry.setdefault("services", {}).pop(entry_id, None)
    core_v1().patch_namespaced_config_map(
        REGISTRY_CONFIGMAP,
        namespace,
        client.V1ConfigMap(data=dump_registry(registry)),
    )


def ensure_namespace_access(target_namespace: str, server_name: str, owner_namespace: str) -> None:
    role = client.V1Role(
        metadata=client.V1ObjectMeta(
            name=full_access_role_name(server_name),
            labels=labels(server_name, owner_namespace),
        ),
        rules=[
            client.V1PolicyRule(
                api_groups=["*"],
                resources=["*"],
                verbs=["*"],
            )
        ],
    )
    binding = client.V1RoleBinding(
        metadata=client.V1ObjectMeta(
            name=role_binding_name(server_name),
            labels=labels(server_name, owner_namespace),
        ),
        role_ref=client.V1RoleRef(
            api_group="rbac.authorization.k8s.io",
            kind="Role",
            name=full_access_role_name(server_name),
        ),
        subjects=[
            client.V1Subject(
                kind="ServiceAccount",
                name=SERVER_SERVICE_ACCOUNT,
                namespace=OPERATOR_NAMESPACE,
            )
        ],
    )
    try:
        rbac_v1().read_namespaced_role(role.metadata.name, target_namespace)
        rbac_v1().patch_namespaced_role(role.metadata.name, target_namespace, role)
    except ApiException as exc:
        if exc.status != 404:
            raise
        rbac_v1().create_namespaced_role(target_namespace, role)

    try:
        rbac_v1().read_namespaced_role_binding(binding.metadata.name, target_namespace)
        rbac_v1().patch_namespaced_role_binding(binding.metadata.name, target_namespace, binding)
    except ApiException as exc:
        if exc.status != 404:
            raise
        rbac_v1().create_namespaced_role_binding(target_namespace, binding)


def ensure_ingress(namespace: str, name: str, spec: dict) -> str:
    ingress = spec.get("ingress") or {}
    host = ingress.get("host") or f"{APP_NAME}.68.220.202.177.nip.io"
    path = ingress.get("path") or "/mcp"
    ingress_class = ingress.get("ingressClassName") or DEFAULT_INGRESS_CLASS
    tls = ingress.get("tls") or {}
    tls_enabled = bool(tls.get("enabled", True))
    tls_secret = tls.get("secretName") or f"{name}-tls"

    annotations = {
        "cert-manager.io/cluster-issuer": DEFAULT_CLUSTER_ISSUER,
    }
    metadata = client.V1ObjectMeta(
        name=ingress_name(name),
        labels=labels(name, namespace),
        annotations=annotations if tls_enabled else {},
    )
    body = client.V1Ingress(
        metadata=metadata,
        spec=client.V1IngressSpec(
            ingress_class_name=ingress_class,
            tls=[
                client.V1IngressTLS(hosts=[host], secret_name=tls_secret)
            ]
            if tls_enabled
            else None,
            rules=[
                client.V1IngressRule(
                    host=host,
                    http=client.V1HTTPIngressRuleValue(
                        paths=[
                            client.V1HTTPIngressPath(
                                path=path,
                                path_type="Prefix",
                                backend=client.V1IngressBackend(
                                    service=client.V1IngressServiceBackend(
                                        name=SERVER_SERVICE,
                                        port=client.V1ServiceBackendPort(number=DEFAULT_SERVER_PORT),
                                    )
                                ),
                            )
                        ]
                    ),
                )
            ],
        ),
    )
    try:
        networking_v1().read_namespaced_ingress(metadata.name, namespace)
        networking_v1().patch_namespaced_ingress(metadata.name, namespace, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        networking_v1().create_namespaced_ingress(namespace, body)
    scheme = "https" if tls_enabled else "http"
    return f"{scheme}://{host}{path}"


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_: object) -> None:
    settings.persistence.finalizer = "mcp.k8s.io/finalizer"


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
def reconcile(spec: dict, name: str, namespace: str, patch: dict, **_: object) -> None:
    ensure_service_account(OPERATOR_NAMESPACE)
    ensure_registry_configmap(OPERATOR_NAMESPACE)
    ensure_shared_deployment(OPERATOR_NAMESPACE)
    ensure_shared_service(OPERATOR_NAMESPACE)

    namespaces = spec.get("namespaces") or []
    if not namespaces:
        raise kopf.PermanentError("spec.namespaces must contain at least one namespace")

    for target_namespace in namespaces:
        ensure_namespace_access(target_namespace, name, namespace)

    url = ensure_ingress(namespace, name, spec)
    auth = spec.get("auth") or {}
    basic = auth.get("basic") or {}
    entry = ServiceEntry(
        service_id=service_id(namespace, name),
        host=(spec.get("ingress") or {}).get("host") or f"{APP_NAME}.68.220.202.177.nip.io",
        path=(spec.get("ingress") or {}).get("path") or "/mcp",
        namespaces=namespaces,
        auth_type=auth.get("type") or "basic",
        basic_secret_name=basic.get("secretName"),
    )
    update_registry(OPERATOR_NAMESPACE, entry)

    patch.status["observedGeneration"] = _.get("body", {}).get("metadata", {}).get("generation")
    patch.status["serviceId"] = entry.service_id
    patch.status["url"] = url
    patch.status["namespaces"] = [{"name": item, "ready": True} for item in namespaces]
    patch.status["conditions"] = [
        {"type": "Ready", "status": "True", "reason": "Reconciled", "message": "MCP service is available"},
        {"type": "SharedRuntimeReady", "status": "True"},
        {"type": "NamespaceAccessReady", "status": "True"},
        {"type": "IngressReady", "status": "True"},
        {"type": "RegistrySynced", "status": "True"},
    ]


@kopf.on.delete(GROUP, VERSION, PLURAL)
def delete(name: str, namespace: str, spec: dict, **_: object) -> None:
    remove_registry_entry(OPERATOR_NAMESPACE, service_id(namespace, name))
    try:
        networking_v1().delete_namespaced_ingress(ingress_name(name), namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
    for target_namespace in spec.get("namespaces") or []:
        try:
            rbac_v1().delete_namespaced_role(full_access_role_name(name), target_namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
        try:
            rbac_v1().delete_namespaced_role_binding(role_binding_name(name), target_namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
