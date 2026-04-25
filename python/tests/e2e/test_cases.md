# V1 E2E Test Cases

The executable tests in this directory validate the first single-namespace version.

Required behavior:

1. The ingress endpoint responds at `MCP_BASE_URL`.
2. Basic auth protects the endpoint.
3. `initialize` returns server information.
4. `tools/list` returns the expected Kubernetes tools.
5. `k8s_api_resources` returns `automationx` as an allowed namespace.
6. `k8s_list` can list pods in `automationx`.
7. `k8s_list` rejects namespaces outside the logical service.

Additional comprehensive operator flow:

8. When `MCP_OPERATOR_E2E=1`, the test installs the CRD, operator RBAC, auth Secret, and operator Deployment.
9. The test creates an `MCPKubernetesServer` CR with a generated ingress host and a specific allowed namespace.
10. The ingress created by the CR must serve a working FastMCP endpoint.
11. `k8s_api_resources` and `k8s_list` must succeed only for the namespace declared in the CR.
