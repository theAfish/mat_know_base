# Workflow Lifecycle Policy

As of June 30, 2026, the active workflow model is the workflow-card extraction
path documented in `workflow-card-architecture.md`.

## Active

- Raw workflow extraction records are active. They store the evidence-grounded
  workflow graph produced from a project.
- Workflow card/schema validation, review, indexing, and ontology induction are
  active.
- Schema proposal review and raw-workflow maintenance tasks are active.

## Compatibility Only

- Canonical workflow records remain readable/deletable so older data, tests,
  and UI tabs continue to work during migration.
- Canonicalization agent/tool code is internal compatibility for unfinished
  legacy jobs and old records. It is not exposed through the active REST/job
  action flow.
- New product behavior should not depend on canonicalization unless it is
  explicitly maintaining compatibility with existing records.
- Canonical workflow code should move behind a `legacy` or `compatibility`
  service boundary before any larger deletion.

## Deprecated For New Work

- The old extraction-to-canonicalization pipeline is retired for new feature
  development.
- New workflow features should consume raw/card graphs and schema-library
  helpers directly.

## Public Surface Status

- Active: raw workflow extraction, raw workflow review/correction, schema
  curation, schema proposal review, raw workflow maintenance, workflow-card
  editing helpers.
- Compatibility: canonical workflow list/get/delete, canonical workflow
  frontend tabs, canonical indexes.
- Internal compatibility: checkpoint and draft-edit helpers used to resume or
  inspect unfinished legacy canonicalization jobs.
