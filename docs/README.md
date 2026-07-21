# Documentation

Choose the guide for the work you are doing.

## Users and API consumers

- [Python API](python-api.md): supported facade, lifecycle, arguments, results, and examples
- [Generated Python API reference](api-reference.md): typed models and grouped methods
- [HTTP API contract](api-contract.md): routes, authentication, jobs, errors, and compatibility
- [Workflow lifecycle policy](workflow-lifecycle-policy.md): current and legacy workflow surfaces

## Contributors

- [Developer setup](development.md): clean-clone setup, commands, tests, and migrations
- [Architecture and ownership](architecture-map.md): boundaries and review ownership
- [Workflow card architecture](workflow-card-architecture.md)
- [Transaction boundaries](transactions.md)
- [Contributing](../CONTRIBUTING.md)

## Operators and security reviewers

- [Operator runbook](operator-runbook.md): startup, diagnostics, shutdown, and incidents
- [Backup and restore](backup-restore.md)
- [Upgrade and migration](upgrades.md)
- [Security model](security.md)
- [Security reporting policy](../SECURITY.md)
- [Supported versions](../SUPPORT.md)

The React frontend under `frontend/` is current. `src/mkb/ui/` (Streamlit) and
legacy canonical-workflow compatibility paths are maintained only to avoid breaking
existing callers; new product and API work targets React and the service layer.
