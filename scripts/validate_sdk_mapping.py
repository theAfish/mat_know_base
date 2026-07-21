"""Compare legacy PostgreSQL rows with the configured typed SDK, without writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from mkb import KnowledgeBase
from mkb.db.models import (
    Asset,
    BackgroundJob,
    CustomSkill,
    Feedback,
    KnowledgeFrame,
    PostProcessorScript,
    ProcessedAsset,
    Projection,
    ProjectGroup,
    RawWorkflowExtraction,
    ResearchProject,
    Space,
)


def _all(service, **filters):
    rows = []
    offset = 0
    while True:
        page = service.list(limit=1000, offset=offset, **filters)
        rows.extend(page)
        if len(page) < 1000:
            return rows
        offset += len(page)


def _mapping(name, expected, actual):
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    return {
        "name": name,
        "ok": not missing and not unexpected,
        "legacy_count": len(expected),
        "sdk_count": len(actual),
        "missing_ids": missing,
        "unexpected_ids": unexpected,
    }


def validate(sample_size: int = 10) -> dict:
    if sample_size < 1 or sample_size > 100:
        raise ValueError("sample_size must be between 1 and 100")
    with KnowledgeBase.from_environment() as kb, kb.database.session() as session:
        def ids(model, column):
            return {str(value) for value in session.scalars(select(column))}

        mappings = [
            _mapping(
                "collections",
                ids(ResearchProject, ResearchProject.project_id),
                {str(item.id) for item in _all(kb.collections)},
            ),
            _mapping(
                "collection_groups",
                ids(ProjectGroup, ProjectGroup.group_id),
                {str(item.id) for item in _all(kb.collections.groups)},
            ),
            _mapping(
                "sources",
                ids(Asset, Asset.asset_id),
                {str(item.id) for item in _all(kb.sources)},
            ),
            _mapping(
                "artifacts",
                ids(ProcessedAsset, ProcessedAsset.processed_asset_id),
                {str(item.id) for item in _all(kb.artifacts)},
            ),
            _mapping(
                "records",
                ids(KnowledgeFrame, KnowledgeFrame.frame_id),
                {str(item.id) for item in _all(kb.records)},
            ),
            _mapping(
                "schemas",
                ids(Space, Space.space_id),
                {str(item.id) for item in _all(kb.schemas)},
            ),
            _mapping(
                "projections",
                ids(Projection, Projection.projection_id),
                {
                    str(item.id)
                    for item in _all(
                        kb.projections,
                        include_deleted=True,
                        include_history=True,
                    )
                },
            ),
            _mapping(
                "feedback",
                ids(Feedback, Feedback.feedback_id),
                {str(item.id) for item in _all(kb.feedback)},
            ),
            _mapping(
                "skills",
                ids(CustomSkill, CustomSkill.skill_id),
                {str(item.id) for item in _all(kb.skills)},
            ),
            _mapping(
                "post_processors",
                ids(PostProcessorScript, PostProcessorScript.script_id),
                {str(item.id) for item in _all(kb.post_processors)},
            ),
            _mapping(
                "raw_workflows",
                ids(RawWorkflowExtraction, RawWorkflowExtraction.extraction_id),
                {str(item.id) for item in _all(kb.materials.workflows)},
            ),
            _mapping(
                "jobs",
                ids(BackgroundJob, BackgroundJob.job_id),
                {str(item.id) for item in kb.jobs.list(limit=1000)},
            ),
        ]

        payload_checks = []

        def record_check(name, identifier, equal):
            payload_checks.append({
                "name": name,
                "id": str(identifier),
                "ok": bool(equal),
            })

        for row in session.scalars(
            select(ResearchProject)
            .order_by(ResearchProject.project_id)
            .limit(sample_size)
        ):
            item = kb.collections.require(row.project_id)
            record_check(
                "collection_payload",
                row.project_id,
                item.name == row.label
                and item.source_path == row.source_path
                and item.metadata == dict(row.metadata_ or {}),
            )
        for row in session.scalars(
            select(KnowledgeFrame)
            .order_by(KnowledgeFrame.frame_id)
            .limit(sample_size)
        ):
            item = kb.records.require(row.frame_id)
            record_check(
                "record_payload",
                row.frame_id,
                item.data == row.content and item.collection_id == row.project_id,
            )
        for row in session.scalars(
            select(Space).order_by(Space.space_id).limit(sample_size)
        ):
            item = kb.schemas.require(row.space_id)
            record_check(
                "schema_payload",
                row.space_id,
                item.definition == row.extraction_schema
                and item.system_prompt == row.system_prompt,
            )
        for row in session.scalars(
            select(Projection).order_by(Projection.projection_id).limit(sample_size)
        ):
            item = kb.projections.require(row.projection_id)
            record_check(
                "projection_payload",
                row.projection_id,
                item.data == row.data and item.validation == row.validation_result,
            )
        for row in session.scalars(
            select(RawWorkflowExtraction)
            .order_by(RawWorkflowExtraction.extraction_id)
            .limit(sample_size)
        ):
            item = kb.materials.workflows.require(row.extraction_id)
            record_check(
                "workflow_payload",
                row.extraction_id,
                item.graph == row.graph
                and item.checkpoint == row.checkpoint
                and item.provenance == dict(row.provenance or {}),
            )

        record_export = json.loads(kb.records.export_json(limit=1000))
        projection_export = json.loads(
            kb.projections.export_json(
                include_deleted=True,
                include_history=True,
                limit=1000,
            )
        )
        exports = {
            "records": {
                "ok": len(record_export) == len(ids(KnowledgeFrame, KnowledgeFrame.frame_id)),
                "count": len(record_export),
            },
            "projections": {
                "ok": len(projection_export) == len(ids(Projection, Projection.projection_id)),
                "count": len(projection_export),
            },
        }

    blockers = [item for item in mappings if not item["ok"]]
    blockers.extend(item for item in payload_checks if not item["ok"])
    blockers.extend(
        {"name": f"{name}_export", **result}
        for name, result in exports.items()
        if not result["ok"]
    )
    return {
        "format_version": 1,
        "ok": not blockers,
        "summary": {
            "mappings": len(mappings),
            "payload_samples": len(payload_checks),
            "exports": len(exports),
            "blocker_count": len(blockers),
        },
        "mappings": mappings,
        "payload_checks": payload_checks,
        "exports": exports,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = validate(args.sample_size)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.out is None:
        print(rendered, end="")
    else:
        if args.out.exists():
            raise FileExistsError(f"Refusing to overwrite validation report: {args.out}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.out.with_name(f".{args.out.name}.tmp")
        temporary.write_text(rendered)
        temporary.replace(args.out)
        print(f"Wrote SDK mapping validation report to {args.out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
