# Contributing

Start with `make bootstrap` and `make check`. The default workflow develops the
Python package without provisioning services or installing the frontend. Keep
storage adapters behind `mkb.repositories` / `mkb.ports`, extraction behavior in
`mkb.extraction`, and app-specific code in the optional compatibility surfaces.
See [development](docs/development.md) for the full application workflow.

Use a focused branch and include tests for behavior changes. Update the Python API
and examples whenever public behavior changes. Run app/frontend checks when those
surfaces change.

Database migrations require review from the database/migrations owner. Never edit a
published migration; add a new one and test upgrade plus restore. Changes involving
authentication, authorization, secrets, uploads/archives, executable processors,
CORS, filesystem paths, network access, or destructive endpoints require security
owner review and a short threat/risk note in the pull request.

Do not commit `.env`, credentials, research data, generated exports, logs, or local
database/object-store state. Report vulnerabilities privately according to
[SECURITY.md](SECURITY.md), not in a public issue.
