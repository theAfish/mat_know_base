# Workflow transaction and idempotency boundaries

Database transactions never include S3, filesystem, provider, or model calls. Each
workflow therefore exposes a durable state before external work, writes derived
objects to a staging key, then commits the final database reference. On failure,
the staging object is removed; if cleanup itself fails it is intentionally visible
to `mkb reconcile` as an orphan.

For new portable databases, `KnowledgeBase.transaction()` supplies collection, source,
artifact, record, schema, and projection repositories bound to one SQLAlchemy
transaction. Successful exit commits all relational writes. An exception rolls them
back and performs best-effort deletion of source/artifact objects written in that
scope, in reverse order. Object storage still does not participate in the database
transaction: failed compensation remains detectable as an orphan through
reconciliation, and callers must use stable IDs so retries are safe.

| Workflow | Durable start | Completion boundary | Retry identity / compensation |
|---|---|---|---|
| ingest | project/asset row in `PENDING` | raw object exists and asset is `STORED` | content hash; delete staging object or leave the row retryable |
| process | asset remains immutable, output is staged | processed asset row references promoted output | source checksum + processor version; delete staged output |
| extract | frame/extraction is `IN_PROGRESS` | completed immutable frame/extraction version commits | project + source version; revert/mark failed at cancellation checkpoint |
| project/review | projection is `IN_PROGRESS` | validated payload and terminal status commit together | space version + frame version; mark failed, preserve previous completed version |
| delete | targets are resolved before mutation | DB deletion commits after best-effort object inventory | resource ID; failed object deletion is reported by reconciliation |

Background API actions additionally use `background_jobs.active_key` as a
cross-process lock and accept an idempotency key. Queued/running jobs left by a
dead process become `INTERRUPTED` on startup; they are never silently reported as
successful. Cooperative cancellation is checked by every progress callback before
the next database, processor, storage, or model phase.
