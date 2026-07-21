"""CLI entry point backed by an explicit :class:`mkb.KnowledgeBase` client."""

import argparse
import json
import sys

from mkb.logging_setup import setup_logging

# Configure logging as early as possible so subsequent imports inherit it.
setup_logging()


def _json_dump(obj):
    print(json.dumps(obj, indent=2, default=str))


def _json_report(obj, output_path, label):
    if not output_path:
        _json_dump(obj)
        return
    from pathlib import Path

    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {label}: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(obj, indent=2, default=str) + "\n")
    temporary.replace(output)
    print(f"Wrote {label} to {output}")


def _knowledge_base():
    from mkb import KnowledgeBase

    return KnowledgeBase.from_environment()


# ── Commands ─────────────────────────────────────────────────────


def cmd_setup(args):
    with _knowledge_base() as kb:
        kb.setup()
    print("Database migrated to the Alembic head.")


def cmd_reset_db(args):
    confirm = input(
        "This will DROP all tables. Type 'RESET DATABASE' to confirm: "
    )
    if confirm.strip() != "RESET DATABASE":
        print("Aborted.")
        return
    with _knowledge_base() as kb:
        kb.reset_database(confirm=confirm.strip())
    print("Database reset complete.")


def cmd_ingest(args):
    with _knowledge_base() as kb:
        result = kb.ingest(args.directory, label=args.label)
    _json_dump(result)


def cmd_sync(args):
    with _knowledge_base() as kb:
        if args.project_id:
            result = kb.sync_project(args.project_id)
        else:
            result = kb.sync(args.root_dir)
    _json_dump(result)


def cmd_process(args):
    with _knowledge_base() as kb:
        result = kb.process(project_id=args.project_id)
    _json_dump(result)


def cmd_extract(args):
    with _knowledge_base() as kb:
        result = kb.extract(
            project_id=args.project_id,
            model=args.model,
            verbose=args.verbose,
            max_passes=args.max_passes,
        )
    _json_dump(result)


def cmd_projects(args):
    with _knowledge_base() as kb:
        projects = kb.list_projects(limit=args.limit)
    for p in projects:
        print(f"  {p['project_id']}  {p['frame_status']:<12}  {p['asset_count']} files  {p['label'] or p['source_path'] or ''}")
    print(f"\n{len(projects)} project(s).")


def cmd_assets(args):
    with _knowledge_base() as kb:
        assets = kb.list_assets(project_id=args.project_id, limit=args.limit)
    for a in assets:
        print(f"  {a['asset_id']}  {a['status']:<10}  {a['mime_type']:<30}  {a['filename']}")
    print(f"\n{len(assets)} asset(s).")


def cmd_search(args):
    with _knowledge_base() as kb:
        result = kb.materials.library.search(
            query=args.query,
            limit=args.limit,
            project_id=args.project_id,
        )

    projects = result["projects"]
    assets = result["assets"]

    if not projects and not assets:
        print("No matches found.")
        return

    if projects:
        print("Projects")
        for project in projects:
            label = project["label"] or project["source_path"] or project["project_id"]
            print(f"  {project['project_id']}  {label}")

    if assets:
        if projects:
            print()
        print("Assets")
        for asset in assets:
            project_id = asset["project_id"] or "-"
            print(
                f"  {asset['asset_id']}  {asset['status']:<10}  "
                f"project={project_id}  {asset['filename']}"
            )

    print(f"\n{result['total']} total match(es).")


def cmd_frames(args):
    with _knowledge_base() as kb:
        frames = kb.materials.frames.list()
    for f in frames:
        print(f"  {f['project_id']}  {f['status']:<12}  v{f.get('extraction_version', 0)}  checked={f['times_checked']}  {f['extraction_summary'] or '':.60s}")
    print(f"\n{len(frames)} frame(s).")


def cmd_frame(args):
    with _knowledge_base() as kb:
        frame = kb.materials.frames.get(args.project_id)
    if not frame:
        print(f"No frame found for project {args.project_id}.")
        sys.exit(1)
    _json_dump(frame)


def cmd_extraction_history(args):
    with _knowledge_base() as kb:
        history = kb.materials.frames.history(args.project_id)
    if not history:
        print(f"No extraction history for project {args.project_id}.")
        return
    for h in history:
        print(f"  Pass {h['pass_number']} ({h['pass_type']})  {h['created_at'] or ''}")
        if h.get("changes_made"):
            print(f"    Changes: {h['changes_made']}")
        if h.get("agent_notes"):
            print(f"    Notes: {h['agent_notes'][:100]}")
    print(f"\n{len(history)} pass(es).")


# ── Space commands ───────────────────────────────────────────────


def cmd_space_create(args):
    import json as _json

    schema = _json.loads(args.schema_file.read()) if args.schema_file else {}
    field_descs = _json.loads(args.field_descriptions.read()) if args.field_descriptions else {}

    with _knowledge_base() as kb:
        result = kb.materials.spaces.create(
            name=args.name,
            domain=args.domain,
            extraction_schema=schema,
            system_prompt=args.system_prompt or "",
            field_descriptions=field_descs,
            description=args.description,
        )
    _json_dump(result)


def cmd_space_create_from_file(args):
    with _knowledge_base() as kb:
        result = kb.materials.spaces.import_file(args.file)
    _json_dump(result)


def cmd_space_list(args):
    with _knowledge_base() as kb:
        spaces = kb.materials.spaces.list()
    for s in spaces:
        print(f"  {s['space_id']}  {s['name']:<20}  v{s['version']}  {s['domain']}")
    print(f"\n{len(spaces)} space(s).")


def cmd_space_show(args):
    with _knowledge_base() as kb:
        space = kb.materials.spaces.get(args.space)
    if not space:
        print(f"Space '{args.space}' not found.")
        sys.exit(1)
    _json_dump(space)


# ── Projection commands ──────────────────────────────────────────


def cmd_project_run(args):
    with _knowledge_base() as kb:
        if args.all:
            result = kb.materials.projections.run_all(
                space_id=args.space,
                model=args.model,
                verbose=args.verbose,
            )
        else:
            result = kb.materials.projections.run(
                space_id=args.space,
                project_id=args.project_id,
                frame_id=args.frame_id,
                model=args.model,
                verbose=args.verbose,
            )
    _json_dump(result)


def cmd_projections(args):
    with _knowledge_base() as kb:
        projections = kb.materials.projections.list(space_id=args.space_id)
    for p in projections:
        reviewed = f"  reviewed={p['times_reviewed']}" if p.get('times_reviewed') else ""
        print(f"  {p['projection_id']}  {p['status']:<16}  space={p['space_id'][:8]}  frame={p['frame_id'][:8]}  v{p['space_version']}{reviewed}")
    print(f"\n{len(projections)} projection(s).")


def cmd_projection_show(args):
    with _knowledge_base() as kb:
        proj = kb.materials.projections.get(args.projection_id)
    if not proj:
        print(f"Projection {args.projection_id} not found.")
        sys.exit(1)
    _json_dump(proj)


def cmd_export_qa_bench(args):
    from mkb import MKBError

    if not args.projection_id and not args.space:
        print("error: provide --projection-id or --space", file=sys.stderr)
        sys.exit(2)

    try:
        if args.projection_id:
            with _knowledge_base() as kb:
                result = kb.materials.projections.export_projection(
                    args.projection_id, args.out, overwrite=args.overwrite
                )
        else:
            with _knowledge_base() as kb:
                result = kb.materials.projections.export_space(
                    args.space, args.out, overwrite=args.overwrite
                )
    except MKBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    for path in result["files"]:
        print(f"wrote {path}")
    for item in result["skipped"]:
        print(f"skipped {item}")
    for warn in result["warnings"]:
        print(f"warning: {warn}", file=sys.stderr)
    print(f"\n{len(result['files'])} file(s) written, {len(result['skipped'])} skipped.")


def cmd_kg_extract(args):
    with _knowledge_base() as kb:
        result = kb.materials.graph.extract(
            project_id=args.project_id,
            frame_id=args.frame_id,
            model=args.model,
            verbose=args.verbose,
            clear_existing=not args.no_clear_existing,
            clear_legacy_frame_sections=not args.keep_legacy_frame_graphs,
        )
    _json_dump(result)


def cmd_kg_clear(args):
    with _knowledge_base() as kb:
        result = kb.materials.graph.clear(
            project_id=args.project_id,
            remove_legacy_frame_sections=not args.keep_legacy_frame_graphs,
        )
    _json_dump(result)


def cmd_kg_show(args):
    with _knowledge_base() as kb:
        result = kb.materials.graph.get(project_id=args.project_id)
    _json_dump(result)


def cmd_kg_review(args):
    with _knowledge_base() as kb:
        result = kb.materials.graph.review(
            mode=args.mode,
            model=args.model,
            verbose=args.verbose,
            seed_count=args.seed_count,
        )
    _json_dump(result)


def cmd_kg_review_counts(args):
    with _knowledge_base() as kb:
        result = kb.materials.graph.review_counts()
    concepts = result.get("concepts", {})
    relations = result.get("relations", {})
    print(f"Concepts reviewed: {len(concepts)}")
    for key, counts in sorted(concepts.items(), key=lambda x: -x[1].get("times_examined", 0))[:20]:
        print(f"  {counts.get('times_examined', 0):3d}x examined  {counts.get('times_modified', 0):3d}x modified  {key}")
    if len(concepts) > 20:
        print(f"  ... ({len(concepts) - 20} more)")
    print(f"\nRelations reviewed: {len(relations)}")
    for key, counts in sorted(relations.items(), key=lambda x: -x[1].get("times_examined", 0))[:20]:
        print(f"  {counts.get('times_examined', 0):3d}x examined  {counts.get('times_modified', 0):3d}x modified  {key}")
    if len(relations) > 20:
        print(f"  ... ({len(relations) - 20} more)")


# ── Feedback commands ────────────────────────────────────────────


def cmd_feedback(args):
    with _knowledge_base() as kb:
        if args.summary:
            projects = kb.list_projects(limit=200)
            summaries = {
                p["project_id"]: kb.materials.feedback.summary(p["project_id"])
                for p in projects
            }
        else:
            items = kb.materials.feedback.list(
                project_id=args.project_id, status=args.status
            )
            projects = kb.list_projects(limit=200) if not args.project_id else []

    if args.summary:
        for p in projects:
            summary = summaries[p["project_id"]]
            if summary["total"] == 0:
                continue
            pid = str(p["project_id"])
            label = p["label"] or p["source_path"] or pid[:12]
            if len(label) > 40:
                label = label[:37] + "..."
            by_status = " ".join(f"{s}={c}" for s, c in summary["by_status"].items())
            by_category = " ".join(f"{c}={n}" for c, n in summary["by_category"].items())
            print(f"  {pid}  {label:<43}  ({summary['total']} items)  status: {by_status}  cat: {by_category}")
        return

    project_labels = {}
    if not args.project_id:
        project_labels = {
            p["project_id"]: p["label"] or p["source_path"] or p["project_id"][:12]
            for p in projects
        }

    for fb in items:
        proj_info = ""
        if not args.project_id:
            pid = fb["target_project_id"]
            label = project_labels.get(pid, pid[:12])
            if len(label) > 40:
                label = label[:37] + "..."
            proj_info = f"  project={pid} ({label})"
        print(f"  {fb['feedback_id']}  {fb['status']:<12}  [{fb['category']}]  {fb['question'][:60]}{proj_info}")
    print(f"\n{len(items)} feedback item(s).")


def cmd_review_feedback(args):
    with _knowledge_base() as kb:
        result = kb.materials.feedback.review(
            project_id=args.project_id,
            model=args.model,
            verbose=args.verbose,
        )
    _json_dump(result)


def cmd_resolve_feedback(args):
    with _knowledge_base() as kb:
        result = kb.materials.feedback.resolve(
            feedback_id=args.feedback_id,
            status=args.status,
            notes=args.notes,
        )
    _json_dump(result)


# ── Projection review commands ──────────────────────────────────


def cmd_review_projections(args):
    if not args.all and not args.project_id:
        print("Error: --project-id is required unless --all is specified.")
        sys.exit(1)
    with _knowledge_base() as kb:
        kwargs = {
            "model": args.model,
            "verbose": args.verbose,
        }
        if args.all:
            result = kb.materials.projections.review_all(
                space_id=args.space, **kwargs
            )
        else:
            result = kb.materials.projections.review(
                space_id=args.space,
                project_id=args.project_id,
                **kwargs,
            )
    _json_dump(result)



def cmd_api(args):
    import logging
    import uvicorn

    with _knowledge_base() as kb:
        runtime = kb.settings.runtime()
        warnings = kb.settings.validate_startup(
            host=args.host, log_level=runtime.get("log_level")
        )
    for warning in warnings:
        logging.getLogger("mkb.cli").warning("UNSAFE LOCAL OVERRIDE: %s", warning)

    uvicorn.run(
        "mkb.web.api_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def cmd_processed(args):
    with _knowledge_base() as kb:
        processed = kb.list_processed_assets(
            project_id=args.project_id, limit=args.limit
        )
    for p in processed:
        target = p["primary_relpath"] or p["s3_key"]
        print(
            f"  {p['asset_id']}  {p['processing_type']:<10}  "
            f"{p['output_format']:<8}  {target}"
        )
    print(f"\n{len(processed)} processed asset(s).")


def cmd_debug_link_processed(args):
    with _knowledge_base() as kb:
        result = kb.materials.library.link_processed(
            processed_dir=args.processed_dir,
            paper_dir=args.paper_dir,
            project_id=args.project_id,
            asset_id=args.asset_id,
            primary_file=args.primary_file,
            processing_type=args.processing_type,
            output_format=args.output_format,
        )
    _json_dump(result)


def cmd_workflow_review(args):
    with _knowledge_base() as kb:
        result = kb.materials.workflows.review(
            args.extraction_id, status=args.status, author=args.author
        )
    _json_dump(result)


def cmd_workflow_correct(args):
    graph = json.loads(args.graph_file.read())
    with _knowledge_base() as kb:
        result = kb.materials.workflows.correct(
            args.extraction_id,
            graph,
            reason=args.reason,
            author=args.author,
            affected_nodes=args.affected_node,
            affected_edges=args.affected_edge,
            evidence=args.evidence,
        )
    _json_dump(result)


def cmd_schema_curate(args):
    with _knowledge_base() as kb:
        result = kb.materials.workflows.curate_schema(
            min_support=args.min_support, author=args.author
        )
    _json_dump(result)


def cmd_schema_proposals(args):
    with _knowledge_base() as kb:
        result = kb.materials.workflows.list_schema_proposals(status=args.status)
    _json_dump(result)


def cmd_schema_review(args):
    with _knowledge_base() as kb:
        result = kb.materials.workflows.review_schema_proposal(
            args.proposal_id, approve=args.approve, reviewer=args.reviewer
        )
    _json_dump(result)


def cmd_workflow_reextract(args):
    scope = {"type": args.scope}
    if args.selector:
        scope["selector"] = args.selector
    with _knowledge_base() as kb:
        result = kb.materials.workflows.schedule_reextraction(
            args.project_id,
            reason=args.reason,
            requested_by=args.requested_by,
            scope=scope,
            raw_extraction_id=args.raw_extraction_id,
        )
        if args.run and result.get("task_id"):
            result = kb.materials.workflows.run_task(
                result["task_id"], model=args.model, verbose=args.verbose
            )
    _json_dump(result)


def cmd_workflow_tasks(args):
    with _knowledge_base() as kb:
        result = kb.materials.workflows.list_tasks(
            status=args.status, project_id=args.project_id
        )
    _json_dump(result)


def cmd_workflow_task_run(args):
    with _knowledge_base() as kb:
        result = kb.materials.workflows.run_task(
            args.task_id, model=args.model, verbose=args.verbose
        )
    _json_dump(result)


def cmd_workflow_index(args):
    with _knowledge_base() as kb:
        result = kb.materials.workflows.rebuild_indexes(args.project_id)
    _json_dump(result)


def cmd_workflow_search(args):
    with _knowledge_base() as kb:
        result = kb.materials.workflows.search(
            args.source, args.operation, args.target, args.mode, args.limit
        )
    _json_dump(result)


def cmd_cleanup(args):
    with _knowledge_base() as kb:
        report = kb.maintenance.cleanup(
            older_than_days=args.older_than_days,
            job_days=args.job_days,
            apply=args.apply,
            confirm=args.confirm,
        )
    _json_dump(report.data)


def cmd_reconcile(args):
    with _knowledge_base() as kb:
        report = kb.maintenance.reconcile()
    result = {"ok": report.ok, **report.data}
    if args.summary:
        result = {
            key: result[key]
            for key in (
                "ok",
                "database_references",
                "storage_objects",
                "missing_count",
                "related_count",
                "orphaned_count",
            )
        }
    _json_report(result, args.out, "reconciliation report")
    if not report.ok:
        raise SystemExit(1)


def cmd_verify_content(args):
    with _knowledge_base() as kb:
        report = kb.maintenance.verify_content(sample_size=args.sample_size)
    _json_report(
        {"ok": report.ok, **report.data},
        args.out,
        "content verification report",
    )
    if not report.ok:
        raise SystemExit(1)


def cmd_inventory(args):
    from pathlib import Path

    with _knowledge_base() as kb:
        result = kb.maintenance.migration_inventory(
            include_object_checksums=args.object_checksums,
        ).data
    if not args.out:
        _json_dump(result)
        return

    output = Path(args.out)
    if output.exists() and not args.overwrite:
        raise FileExistsError(
            f"Refusing to overwrite existing inventory: {output}; pass --overwrite explicitly"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(result, indent=2, default=str) + "\n")
    temporary.replace(output)
    print(f"Wrote read-only migration inventory to {output}")


def cmd_migration_preflight(args):
    from pathlib import Path

    with _knowledge_base() as kb:
        report = kb.maintenance.compare_inventories(args.before, args.after)
    result = report.data
    if not args.out:
        _json_dump(result)
    else:
        output = Path(args.out)
        if output.exists() and not args.overwrite:
            raise FileExistsError(
                f"Refusing to overwrite existing preflight report: {output}; "
                "pass --overwrite explicitly"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        temporary.write_text(json.dumps(result, indent=2, default=str) + "\n")
        temporary.replace(output)
        print(f"Wrote read-only migration preflight report to {output}")
    if not report.ok:
        raise SystemExit(1)


def cmd_restore_missing_artifact(args):
    from pathlib import Path

    output = Path(args.ledger) if args.ledger else None
    if args.apply and output is None:
        raise ValueError("--ledger is required when --apply is used")
    if output is not None and output.exists():
        raise FileExistsError(f"Refusing to overwrite migration ledger entry: {output}")
    with _knowledge_base() as kb:
        report = kb.maintenance.restore_missing_artifact(
            args.artifact_id,
            args.local_file,
            apply=args.apply,
            confirm=args.confirm,
        )
    result = {"ok": report.ok, **report.data}
    _json_dump(result)
    if not args.apply:
        return
    assert output is not None
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(result, indent=2, default=str) + "\n")
    temporary.replace(output)
    print(f"Wrote migration repair ledger to {output}")


# ── Argument Parsing ─────────────────────────────────────────────


def main():
    from mkb import MKBConfig

    application_config = MKBConfig.from_environment()
    parser = argparse.ArgumentParser(prog="mkb", description="Materials Knowledge Base")
    sub = parser.add_subparsers(dest="command")

    # setup
    sub.add_parser("setup", help="Create database tables")

    # reset-db
    sub.add_parser("reset-db", help="Drop and recreate all tables")

    # ingest
    p = sub.add_parser("ingest", help="Ingest a single project directory")
    p.add_argument("directory")
    p.add_argument("--label", "-l", default=None)

    # sync
    p = sub.add_parser("sync", help="Sync projects from a root folder (or re-scan one project)")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--root-dir", "-r", metavar="DIR", help="Root folder containing project subfolders")
    grp.add_argument("--project-id", "-p", metavar="UUID", help="Re-scan a single existing project")

    # process
    p = sub.add_parser("process", help="Process assets (all or by project)")
    p.add_argument("--project-id", "-p", default=None)

    # extract
    p = sub.add_parser("extract", help="Run knowledge extraction")
    p.add_argument("--project-id", "-p", default=None)
    p.add_argument("--model", "-m", default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--max-passes", type=int, default=1, help="Number of extraction passes (default: 1)")

    # projects
    p = sub.add_parser("projects", help="List research projects")
    p.add_argument("--limit", "-n", type=int, default=50)

    # assets
    p = sub.add_parser("assets", help="List assets")
    p.add_argument("--project-id", "-p", default=None)
    p.add_argument("--limit", "-n", type=int, default=100)

    # search
    p = sub.add_parser("search", help="Search projects and assets by keyword")
    p.add_argument("query", help="Free-text keyword query")
    p.add_argument("--project-id", "-p", default=None, help="Limit asset/project matches to a single project")
    p.add_argument("--limit", "-n", type=int, default=25)

    # frames
    sub.add_parser("frames", help="List knowledge frames")

    # frame
    p = sub.add_parser("frame", help="Show knowledge frame for a project")
    p.add_argument("project_id")

    # extraction-history
    p = sub.add_parser("extraction-history", help="Show extraction pass history for a project")
    p.add_argument("project_id")

    # ── Space subcommands ──
    space_parser = sub.add_parser("space", help="Manage spaces")
    space_sub = space_parser.add_subparsers(dest="space_command")

    p = space_sub.add_parser("create", help="Create a space from arguments")
    p.add_argument("--name", required=True)
    p.add_argument("--domain", required=True)
    p.add_argument("--description", default=None)
    p.add_argument("--system-prompt", default=None)
    p.add_argument("--schema-file", type=argparse.FileType("r"), default=None, help="JSON file with extraction schema")
    p.add_argument("--field-descriptions", type=argparse.FileType("r"), default=None, help="Legacy JSON guidance file; merged into schema descriptions")

    p = space_sub.add_parser("load", help="Create a space from a JSON file")
    p.add_argument("file", help="Path to JSON space definition file")

    space_sub.add_parser("list", help="List all spaces")

    p = space_sub.add_parser("show", help="Show space details")
    p.add_argument("space", help="Space ID or name")

    # ── Projection subcommands ──
    p = sub.add_parser("project-run", help="Run projection on frames using a space")
    p.add_argument("--space", "-s", required=True, help="Space ID or name")
    p.add_argument("--project-id", "-p", default=None)
    p.add_argument("--frame-id", "-f", default=None)
    p.add_argument("--all", "-a", action="store_true", help="Project all completed frames")
    p.add_argument("--model", "-m", default=None)
    p.add_argument("--verbose", "-v", action="store_true")

    p = sub.add_parser("projections", help="List projections")
    p.add_argument("--space-id", "-s", default=None)

    p = sub.add_parser("projection", help="Show projection details")
    p.add_argument("projection_id")

    p = sub.add_parser(
        "export-qa-bench",
        help="Export qa_benchmark projection(s) to mat_agent_bench YAML files",
    )
    p.add_argument("--projection-id", "-p", default=None, help="Export a single projection")
    p.add_argument("--space", "-s", default=None, help="Export all projections for this space (id or name)")
    p.add_argument("--out", "-o", required=True, help="Output root directory (one YAML per question under <out>/<capability>/)")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing YAML files")

    # ── Knowledge Graph subcommands ──
    p = sub.add_parser("kg-extract", help="Extract concept-only knowledge graphs into the global space")
    p.add_argument("--project-id", "-p", default=None)
    p.add_argument("--frame-id", "-f", default=None)
    p.add_argument("--model", "-m", default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--no-clear-existing", action="store_true", help="Do not clear previous KG projections for the target frame(s)")
    p.add_argument("--keep-legacy-frame-graphs", action="store_true", help="Do not remove legacy graph sections from frame content")

    p = sub.add_parser("kg-clear", help="Clear old extracted knowledge graphs")
    p.add_argument("--project-id", "-p", default=None)
    p.add_argument("--keep-legacy-frame-graphs", action="store_true", help="Do not remove legacy graph sections from frame content")

    p = sub.add_parser("kg-show", help="Show merged global concept graph")
    p.add_argument("--project-id", "-p", default=None)

    p = sub.add_parser("kg-review", help="Run graph review agent (deduplication + quality cleanup)")
    p.add_argument("--mode", default="auto", choices=["auto", "global", "local"],
                   help="Review mode: auto (default), global (relation standardization + concept dedup), local (neighborhood exploration)")
    p.add_argument("--model", "-m", default=None, help="LLM model override")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--seed-count", type=int, default=10, metavar="N",
                   help="Number of starting concepts for local mode (default: 10)")

    sub.add_parser("kg-review-counts", help="Show per-element review counts from graph review runs")

    # ── Feedback subcommands ──
    p = sub.add_parser("feedback", help="List feedback items")
    p.add_argument("--project-id", "-p", default=None)
    p.add_argument("--status", default=None, help="Filter by status (OPEN, RESOLVED, DISMISSED, etc.)")
    p.add_argument("--summary", "-s", action="store_true", help="Show per-project feedback summary")

    p = sub.add_parser("review-feedback", help="Run feedback review on a project")
    p.add_argument("--project-id", "-p", required=True)
    p.add_argument("--model", "-m", default=None)
    p.add_argument("--verbose", "-v", action="store_true")

    p = sub.add_parser("resolve-feedback", help="Manually resolve a feedback item")
    p.add_argument("feedback_id")
    p.add_argument("--status", required=True, help="RESOLVED, DISMISSED, or DEV_ISSUE")
    p.add_argument("--notes", default="", help="Resolution notes")

    # ── Projection Review subcommands ──
    p = sub.add_parser("review-projections", help="Review and consolidate projections for a project")
    p.add_argument("--space", "-s", required=True, help="Space ID or name")
    p.add_argument("--project-id", "-p", default=None)
    p.add_argument("--all", "-a", action="store_true", help="Review all projects in the space")
    p.add_argument("--model", "-m", default=None)
    p.add_argument("--verbose", "-v", action="store_true")


    # ── API ──
    p = sub.add_parser("api", help="Launch the FastAPI backend for the React UI")
    p.add_argument("--host", default=application_config.api_host)
    p.add_argument("--port", type=int, default=application_config.api_port)
    p.add_argument("--reload", action="store_true")

    # processed
    p = sub.add_parser("processed", help="List processed outputs")
    p.add_argument("--project-id", "-p", default=None)
    p.add_argument("--limit", "-n", type=int, default=100)

    # debug-link-processed
    p = sub.add_parser("debug-link-processed", help="Attach a handmade processed folder to a paper/project")
    p.add_argument("--processed-dir", required=True, help="Local processed folder to register")
    p.add_argument("--paper-dir", default=None, help="Paper directory to resolve the target project")
    p.add_argument("--project-id", "-p", default=None, help="Existing project ID to attach to")
    p.add_argument("--asset-id", "-a", default=None, help="Optional raw asset ID override")
    p.add_argument("--primary-file", default=None, help="Optional relative primary file inside the processed dir")
    p.add_argument("--processing-type", default=None, help="Optional override: MARKDOWN, DATAFRAME, IMAGE")
    p.add_argument("--output-format", default=None, help="Optional override for output format")

    p = sub.add_parser("workflow-review", help="Audit or manually classify a raw workflow")
    p.add_argument("extraction_id")
    p.add_argument("--status", choices=["active", "superseded", "retracted", "needs_review", "known_error"])
    p.add_argument("--author", default="cli")

    p = sub.add_parser("workflow-correct", help="Create an immutable corrected raw workflow version")
    p.add_argument("extraction_id")
    p.add_argument("graph_file", type=argparse.FileType("r"))
    p.add_argument("--reason", required=True)
    p.add_argument("--author", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--affected-node", action="append", default=[])
    p.add_argument("--affected-edge", action="append", default=[])

    p = sub.add_parser("schema-curate", help="Analyze workflows and propose schema changes")
    p.add_argument("--min-support", type=int, default=2)
    p.add_argument("--author", default="schema-curator/1.0")

    p = sub.add_parser("schema-proposals", help="List schema proposals")
    p.add_argument("--status", default="pending")

    p = sub.add_parser("schema-review", help="Approve or reject a schema proposal")
    p.add_argument("proposal_id")
    decision = p.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", dest="approve", action="store_false")
    p.add_argument("--reviewer", required=True)

    p = sub.add_parser("workflow-reextract", help="Queue controlled full or partial re-extraction")
    p.add_argument("project_id")
    p.add_argument("--reason", required=True, choices=[
        "extractor_prompt_changed", "low_quality_extraction", "new_parser_capability",
        "manual_review_error", "schema_evolution_missing_information",
    ])
    p.add_argument("--scope", choices=["full", "section", "paragraph", "table", "figure"], default="full")
    p.add_argument("--selector")
    p.add_argument("--raw-extraction-id")
    p.add_argument("--requested-by", default="cli")
    p.add_argument("--run", action="store_true")
    p.add_argument("--model")
    p.add_argument("--verbose", action="store_true")

    p = sub.add_parser("workflow-tasks", help="List workflow maintenance tasks")
    p.add_argument("--status")
    p.add_argument("--project-id")

    p = sub.add_parser("workflow-task-run", help="Run one queued workflow maintenance task")
    p.add_argument("task_id")
    p.add_argument("--model")
    p.add_argument("--verbose", action="store_true")

    p = sub.add_parser("workflow-index", help="Rebuild persisted workflow search indexes")
    p.add_argument("--project-id")

    p = sub.add_parser("workflow-search", help="Search indexed canonical workflows")
    p.add_argument("--source")
    p.add_argument("--operation")
    p.add_argument("--target")
    p.add_argument("--mode", choices=["strict", "alias-expanded", "template-expanded", "granularity-expanded", "evidence-required"], default="strict")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("cleanup", help="Plan or apply retention cleanup")
    p.add_argument("--older-than-days", type=int, default=7)
    p.add_argument("--job-days", type=int, default=30)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm", help="Required exact value DELETE when applying")

    p = sub.add_parser("reconcile", help="Read-only PostgreSQL/MinIO consistency check")
    p.add_argument("--summary", action="store_true", help="Omit individual object keys")
    p.add_argument("--out", help="Write a new JSON report instead of printing")

    p = sub.add_parser(
        "verify-content",
        help="Verify references and checksum deterministic source/artifact samples",
    )
    p.add_argument("--sample-size", type=int, default=10)
    p.add_argument("--out", help="Write a new JSON report instead of printing")

    p = sub.add_parser("inventory", help="Write a read-only local data migration inventory")
    p.add_argument("--out", help="JSON output path; prints to stdout when omitted")
    p.add_argument("--overwrite", action="store_true", help="Replace an existing output file")
    p.add_argument(
        "--object-checksums",
        action="store_true",
        help="Stream every object and include SHA-256 content digests",
    )

    p = sub.add_parser(
        "migration-preflight",
        help="Compare pre/post inventories and fail on missing or changed data",
    )
    p.add_argument("before", help="Pre-migration inventory JSON")
    p.add_argument("after", help="Post-migration inventory JSON")
    p.add_argument("--out", help="JSON output path; prints to stdout when omitted")
    p.add_argument("--overwrite", action="store_true", help="Replace an existing report")

    p = sub.add_parser(
        "restore-missing-artifact",
        help="Checksum-gate restoration of one missing object from a local mirror",
    )
    p.add_argument("artifact_id")
    p.add_argument("local_file")
    p.add_argument("--apply", action="store_true", help="Upload after all checks pass")
    p.add_argument("--confirm", help="Required exact value RESTORE MISSING OBJECT")
    p.add_argument("--ledger", help="New JSON ledger path; required with --apply")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmd_map = {
        "setup": cmd_setup,
        "reset-db": cmd_reset_db,
        "ingest": cmd_ingest,
        "sync": cmd_sync,
        "process": cmd_process,
        "extract": cmd_extract,
        "projects": cmd_projects,
        "assets": cmd_assets,
        "search": cmd_search,
        "frames": cmd_frames,
        "frame": cmd_frame,
        "processed": cmd_processed,
        "debug-link-processed": cmd_debug_link_processed,
        "workflow-review": cmd_workflow_review,
        "workflow-correct": cmd_workflow_correct,
        "schema-curate": cmd_schema_curate,
        "schema-proposals": cmd_schema_proposals,
        "schema-review": cmd_schema_review,
        "workflow-reextract": cmd_workflow_reextract,
        "workflow-tasks": cmd_workflow_tasks,
        "workflow-task-run": cmd_workflow_task_run,
        "workflow-index": cmd_workflow_index,
        "workflow-search": cmd_workflow_search,
        "cleanup": cmd_cleanup,
        "reconcile": cmd_reconcile,
        "verify-content": cmd_verify_content,
        "inventory": cmd_inventory,
        "migration-preflight": cmd_migration_preflight,
        "restore-missing-artifact": cmd_restore_missing_artifact,
        "extraction-history": cmd_extraction_history,
        "project-run": cmd_project_run,
        "projections": cmd_projections,
        "projection": cmd_projection_show,
        "export-qa-bench": cmd_export_qa_bench,
        "kg-extract": cmd_kg_extract,
        "kg-clear": cmd_kg_clear,
        "kg-show": cmd_kg_show,
        "kg-review": cmd_kg_review,
        "kg-review-counts": cmd_kg_review_counts,
        "feedback": cmd_feedback,
        "review-feedback": cmd_review_feedback,
        "resolve-feedback": cmd_resolve_feedback,
        "review-projections": cmd_review_projections,
        "api": cmd_api,
    }

    if args.command == "space":
        if not args.space_command:
            space_parser.print_help()
            sys.exit(1)
        space_cmd_map = {
            "create": cmd_space_create,
            "load": cmd_space_create_from_file,
            "list": cmd_space_list,
            "show": cmd_space_show,
        }
        space_cmd_map[args.space_command](args)
    else:
        cmd_map[args.command](args)


if __name__ == "__main__":
    main()
