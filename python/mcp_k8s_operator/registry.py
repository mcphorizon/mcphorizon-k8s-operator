from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from .config import REGISTRY_KEY


@dataclass(frozen=True)
class BasicAuthConfig:
    secret_name: str


@dataclass(frozen=True)
class ServiceEntry:
    service_id: str
    host: str
    path: str
    namespaces: list[str]
    auth_type: str
    basic_secret_name: str | None = None

    @classmethod
    def from_dict(cls, service_id: str, data: dict[str, Any]) -> "ServiceEntry":
        auth = data.get("auth") or {}
        basic = auth.get("basic") or {}
        return cls(
            service_id=service_id,
            host=data["host"],
            path=data.get("path") or "/mcp",
            namespaces=list(data.get("namespaces") or []),
            auth_type=auth.get("type") or "basic",
            basic_secret_name=basic.get("secretName") or auth.get("secretName"),
        )

    def to_dict(self) -> dict[str, Any]:
        auth: dict[str, Any] = {"type": self.auth_type}
        if self.auth_type == "basic" and self.basic_secret_name:
            auth["basic"] = {"secretName": self.basic_secret_name}
        return {
            "host": self.host,
            "path": self.path,
            "namespaces": self.namespaces,
            "auth": auth,
        }


def empty_registry() -> dict[str, Any]:
    return {"services": {}}


def parse_registry(configmap_data: dict[str, str] | None) -> dict[str, Any]:
    if not configmap_data or not configmap_data.get(REGISTRY_KEY):
        return empty_registry()
    loaded = yaml.safe_load(configmap_data[REGISTRY_KEY]) or {}
    if "services" not in loaded or loaded["services"] is None:
        loaded["services"] = {}
    return loaded


def dump_registry(registry: dict[str, Any]) -> dict[str, str]:
    return {REGISTRY_KEY: yaml.safe_dump(registry, sort_keys=True)}


def service_entries(registry: dict[str, Any]) -> dict[str, ServiceEntry]:
    services = registry.get("services") or {}
    return {
        service_id: ServiceEntry.from_dict(service_id, service_data)
        for service_id, service_data in services.items()
    }
