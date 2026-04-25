from __future__ import annotations

import os

import httpx

from .conftest import call_tool, rpc, tool_text


def test_basic_auth_required() -> None:
    response = httpx.post(
        os.getenv("MCP_BASE_URL", "https://mcpoperator.68.220.202.177.nip.io/mcp/"),
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        timeout=30,
        verify=False,
        follow_redirects=True,
    )
    assert response.status_code in (401, 403)


def test_initialize(base_url: str) -> None:
    _, result = rpc(
        base_url,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    )
    assert result["result"]["serverInfo"]["name"] == "mcp-k8s-server"


def test_tools_list_contains_v1_tools(base_url: str, session_id: str) -> None:
    _, result = rpc(base_url, "tools/list", session_id=session_id, request_id=2)
    names = {tool["name"] for tool in result["result"]["tools"]}
    assert "k8s_api_resources" in names
    assert "k8s_list" in names
    assert "k8s_get" in names
    assert "k8s_logs" in names


def test_api_resources_includes_allowed_namespace(base_url: str, session_id: str, allowed_namespace: str) -> None:
    result = call_tool(base_url, session_id, "k8s_api_resources", {})
    body = tool_text(result)
    assert allowed_namespace in body["namespaces"]
    assert any(item["resource"] == "pods" for item in body["resources"])


def test_can_list_pods_in_allowed_namespace(base_url: str, session_id: str, allowed_namespace: str) -> None:
    result = call_tool(base_url, session_id, "k8s_list", {"resource": "pods", "namespace": allowed_namespace})
    body = tool_text(result)
    assert body["kind"] == "PodList"
    assert "items" in body


def test_rejects_disallowed_namespace(base_url: str, session_id: str) -> None:
    result = call_tool(base_url, session_id, "k8s_list", {"resource": "pods", "namespace": "default"})
    assert result["error"]["code"] == -32602
    assert "not allowed" in result["error"]["message"]
