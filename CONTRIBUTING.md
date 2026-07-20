# Contributing

Start with `make bootstrap`, review `.env`, then run `make up`, `make doctor`, and
`make check`. Keep adapters thin: domain behavior belongs in `src/mkb/services/`, HTTP
mapping in `src/mkb/web/`, CLI parsing in `src/mkb/cli.py`, and current UI work in
`frontend/`. Streamlit and canonical-workflow compatibility paths are legacy.

Use a focused branch and include tests for behavior changes. Before opening a pull
request, run `make lint`, `make test`, and `make test-frontend`. Update the Python API,
HTTP contract, operator, or security docs whenever their observable behavior changes.

Database migrations require review from the database/migrations owner. Never edit a
published migration; add a new one and test upgrade plus restore. Changes involving
authentication, authorization, secrets, uploads/archives, executable processors,
CORS, filesystem paths, network access, or destructive endpoints require security
owner review and a short threat/risk note in the pull request.

Do not commit `.env`, credentials, research data, generated exports, logs, or local
database/object-store state. Report vulnerabilities privately according to
[SECURITY.md](SECURITY.md), not in a public issue.

