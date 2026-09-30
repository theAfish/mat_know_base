# HTTP API contract

FastAPI serves the current React client and external HTTP callers. When running, the
authoritative OpenAPI document is `/openapi.json` and interactive documentation is
`/docs`. Treat that generated schema as authoritative for request and response fields;
this guide defines cross-route behavior.

All application data routes use the `/api` prefix. `/health`, `/health/live`, and
`/health/ready` are public operational probes. Readiness checks the database revision,
object-storage buckets, and worker availability.

When authentication is enabled, send `Authorization: Bearer <token>`. Roles are
reader (reads), editor (mutations/job starts), and admin (deletes, settings, executable
uploads). See the [security model](security.md).

Long operations such as processing, extraction, projection, graph work, and review
return durable job records rather than holding the HTTP request open. Poll
`GET /api/jobs/{job_id}`, consume its status/progress/error fields, and treat only a
terminal successful state as completion. Cancellation is cooperative via
`POST /api/jobs/{job_id}/cancel`; a cancellation response does not imply the worker has
already stopped.

Collections use JSON arrays or documented wrapper objects. Missing resources return
404. Validation failures use FastAPI's 422 response. Authentication/authorization use
401/403. Conflicts and unsafe state transitions may use 409. Dependency and internal
failures use categorized error details without credentials. Clients must tolerate
additive response fields and should not depend on error prose.

Route families cover projects/assets, frames, spaces, projections, graph, feedback,
jobs, skills, settings, assistant, post-processors, workflow extraction/maintenance,
and project groups. The TypeScript modules under `frontend/src/api/` are useful current
examples, but OpenAPI is the external contract. React is the bundled client.

## Versioned knowledge integration

The stable `/api/v1` boundary exposes reviewed knowledge drafts, facts, and durable
integration events. `POST /api/v1/correction-requests` accepts a complete KDG
correction detail and imports it idempotently as an editable MKB draft linked to the
target fact revision. The initial draft copies the authoritative fact payload and MKB
evidence references; external KDG provenance remains in `correction_context`.
`GET /api/v1/facts/{fact_id}` reads one immutable published fact revision by its
UUID, returning 404 for an unknown ID. Its `revision` is the number within the
fact set; use the returned ID and revision when citing it from another service.

Correction intake is an editor-level mutation and never changes a fact directly. The
draft must follow the normal revise, submit-review, and approval workflow. Approval
appends the next revision to the existing fact set, marks the prior revision
`SUPERSEDED`, and emits `fact.revision.published` with
`supersedes_fact_revision_id`. Approval and rejection continue to require publish
permission.
