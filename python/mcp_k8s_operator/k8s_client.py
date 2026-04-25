from __future__ import annotations

from kubernetes import client, config


def load_kubernetes_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def api_client() -> client.ApiClient:
    load_kubernetes_config()
    return client.ApiClient()


def core_v1() -> client.CoreV1Api:
    return client.CoreV1Api(api_client())


def apps_v1() -> client.AppsV1Api:
    return client.AppsV1Api(api_client())


def networking_v1() -> client.NetworkingV1Api:
    return client.NetworkingV1Api(api_client())


def rbac_v1() -> client.RbacAuthorizationV1Api:
    return client.RbacAuthorizationV1Api(api_client())


def custom_objects() -> client.CustomObjectsApi:
    return client.CustomObjectsApi(api_client())
