This folder stores the working Kubernetes manifests used for the live deployment workflow in `rules.md`.

Because the available MCP server tools are namespace-scoped and do not expose CRD or RBAC creation in this session, the deployed stack here is the shared MCP server runtime needed by the executable e2e tests:

- source bundle `ConfigMap` for Kaniko builds
- Kaniko `Job` manifests
- runtime `Secret`, `ConfigMap`, `Deployment`, `Service`, and `Ingress`

The Python operator and CRD source remain under `python/`, and the operator code is kept aligned with the V1 spec for future cluster deployments where CRD/RBAC APIs are available.
