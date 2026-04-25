# Python V1 Prototype

This folder contains the first Python/Kopf implementation of the MCP Kubernetes operator.

## Components

- `mcp_k8s_operator.operator`: Kopf operator that watches `MCPKubernetesServer`.
- `mcp_k8s_operator.server`: shared MCP server runtime.
- `k8s-manifests`: Kubernetes manifests required by `rules.md`.
- `tests/e2e`: executable e2e tests derived from `tests/e2e/test_cases.md`.

## V1 Scope

The prototype targets one namespace: `automationx`.

The CRD still models namespaces as a list so the runtime can later expand to multiple namespaces.

## Local Checks

```bash
python -m compileall mcp_k8s_operator
pytest tests/e2e -q
```

The e2e tests expect a deployed endpoint by default:

```bash
MCP_BASE_URL=https://mcpoperator.68.220.202.177.nip.io/mcp/ pytest tests/e2e -q
```

To run the comprehensive CR-driven operator flow, enable the bootstrap test:

```bash
MCP_OPERATOR_E2E=1 pytest tests/e2e/test_operator_install_flow.py -q
```

Useful overrides:

```bash
MCP_OPERATOR_E2E=1 \
MCP_OPERATOR_IMAGE=registry.68.220.202.177.nip.io/mcpoperator-python-operator:latest \
MCP_SERVER_IMAGE=registry.68.220.202.177.nip.io/mcpoperator-python:latest \
MCP_INGRESS_SUFFIX=68.220.202.177.nip.io \
pytest tests/e2e/test_operator_install_flow.py -q
```
