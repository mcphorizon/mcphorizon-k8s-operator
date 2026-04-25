from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx
import pytest


DEFAULT_BASE_URL = os.getenv("MCP_BASE_URL", "https://mcpoperator.68.220.202.177.nip.io/mcp/")
DEFAULT_USERNAME = os.getenv("MCP_USERNAME", "admin")
DEFAULT_PASSWORD = os.getenv("MCP_PASSWORD", "password")
DEFAULT_NAMESPACE = os.getenv("MCP_NAMESPACE", "automationx")


def canonical_base_url(url: str) -> str:
    return url.rstrip("/") + "/"


def auth_headers(username: str = DEFAULT_USERNAME, password: str = DEFAULT_PASSWORD) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def parse_rpc_response(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()

    data_lines = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
    if not data_lines:
        raise AssertionError(f"Unexpected MCP response body: {response.text!r}")
    return json.loads(data_lines[-1])


def rpc(
    base_url: str,
    method: str,
    params: dict[str, Any] | None = None,
    request_id: int = 1,
    session_id: str | None = None,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
) -> tuple[httpx.Response, dict[str, Any]]:
    headers = {
        **auth_headers(username, password),
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    response = httpx.post(
        canonical_base_url(base_url),
        headers=headers,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        timeout=60,
        verify=False,
        follow_redirects=True,
    )
    return response, parse_rpc_response(response)


def initialize_session(
    base_url: str,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
) -> tuple[str, dict[str, Any]]:
    response, payload = rpc(
        base_url,
        "initialize",
        params={
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
        username=username,
        password=password,
    )
    response.raise_for_status()
    session_id = response.headers.get("mcp-session-id")
    assert session_id, "FastMCP initialize did not return mcp-session-id"
    return session_id, payload


def call_tool(
    base_url: str,
    session_id: str,
    name: str,
    arguments: dict[str, Any],
    request_id: int = 10,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
) -> dict[str, Any]:
    _, payload = rpc(
        base_url,
        "tools/call",
        params={"name": name, "arguments": arguments},
        request_id=request_id,
        session_id=session_id,
        username=username,
        password=password,
    )
    return payload


def tool_text(result: dict[str, Any]) -> dict[str, Any]:
    if "error" in result:
        return result
    content = result["result"]["content"][0]
    assert content["type"] == "text"
    return json.loads(content["text"])


@pytest.fixture
def base_url() -> str:
    return canonical_base_url(DEFAULT_BASE_URL)


@pytest.fixture
def session_id(base_url: str) -> str:
    session_id, payload = initialize_session(base_url)
    assert payload["result"]["serverInfo"]["name"] == "mcp-k8s-server"
    return session_id


@pytest.fixture
def allowed_namespace() -> str:
    return DEFAULT_NAMESPACE
