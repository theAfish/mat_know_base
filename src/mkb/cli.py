"""CLI entry point — thin wrapper around mkb.api."""

import argparse
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")


def _json_dump(obj):
    print(json.dumps(obj, indent=2, default=str))


# ── Commands ─────────────────────────────────────────────────────


def cmd_setup(args):
    from mkb.api import setup
    setup()
    print("Database tables created.")


def cmd_reset_db(args):
    from mkb.api import reset_db
    confirm = input("This will DROP all tables. Type 'yes' to confirm: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return
    reset_db()
    print("Database reset complete.")


def cmd_ingest(args):
    from mkb.api import ingest
    result = ingest(args.directory, label=args.label)
    _json_dump(result)


def cmd_sync(args):
    from mkb.api import sync, sync_project
    if args.project_id:
        result = sync_project(args.project_id)
    else:
        result = sync(args.root_dir)
    _json_dump(result)


def cmd_process(args):
    from mkb.api import process
    result = process(project_id=args.project_id)
    _json_dump(result)


def cmd_extract(args):
    from mkb.api import extract
    result = extract(
        project_id=args.project_id,
        model=args.model,
        verbose=args.verbose,
        max_passes=args.max_passes,
    )
    _json_dump(result)


def cmd_projects(args):
    from mkb.api import list_projects
    projects = list_projects(limit=args.limit)
    for p in projects:
        print(f"  {p['project_id']}  {p['frame_status']:<12}  {p['asset_count']} files  {p['label'] or p['source_path'] or ''}")
    print(f"\n{len(projects)} project(s).")


def cmd_assets(args):
    from mkb.api import list_assets
    assets = list_assets(project_id=args.project_id, limit=args.limit)
    for a in assets:
        print(f"  {a['asset_id']}  {a['status']:<10}  {a['mime_type']:<30}  {a['filename']}")
    print(f"\n{len(assets)} asset(s).")


def cmd_frames(args):
    from mkb.api import list_frames
    frames = list_frames()
    for f in frames:
        print(f"  {f['project_id']}  {f['status']:<12}  v{f.get('extraction_version', 0)}  checked={f['times_checked']}  {f['extraction_summary'] or '':.60s}")
    print(f"\n{len(frames)} frame(s).")


def cmd_frame(args):
    from mkb.api import get_frame
    frame = get_frame(args.project_id)
    if not frame:
        print(f"No frame found for project {args.project_id}.")
        sys.exit(1)
    _json_dump(frame)


def cmd_extraction_history(args):
    from mkb.api import get_extraction_history
    history = get_extraction_history(args.project_id)
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
    from mkb.api import create_space

    schema = _json.loads(args.schema_file.read()) if args.schema_file else {}
    field_descs = _json.loads(args.field_descriptions.read()) if args.field_descriptions else {}

    result = create_space(
        name=args.name,
        domain=args.domain,
        extraction_schema=schema,
        system_prompt=args.system_prompt or "",
        field_descriptions=field_descs,
        description=args.description,
    )
    _json_dump(result)


def cmd_space_create_from_file(args):
    from mkb.spaces.registry import load_space_from_file
    result = load_space_from_file(args.file)
    _json_dump(result)


def cmd_space_list(args):
    from mkb.api import list_spaces
    spaces = list_spaces()
    for s in spaces:
        print(f"  {s['space_id']}  {s['name']:<20}  v{s['version']}  {s['domain']}")
    print(f"\n{len(spaces)} space(s).")


def cmd_space_show(args):
    from mkb.api import get_space
    space = get_space(args.space)
    if not space:
        print(f"Space '{args.space}' not found.")
        sys.exit(1)
    _json_dump(space)


# ── Projection commands ──────────────────────────────────────────


def cmd_project_run(args):
    from mkb.api import project, project_all
    if args.all:
        result = project_all(
            space_id=args.space,
            model=args.model,
            verbose=args.verbose,
        )
    else:
        result = project(
            space_id=args.space,
            project_id=args.project_id,
            frame_id=args.frame_id,
            model=args.model,
            verbose=args.verbose,
        )
    _json_dump(result)


def cmd_projections(args):
    from mkb.api import list_projections
    projections = list_projections(space_id=args.space_id)
    for p in projections:
        reviewed = f"  reviewed={p['times_reviewed']}" if p.get('times_reviewed') else ""
        print(f"  {p['projection_id']}  {p['status']:<16}  space={p['space_id'][:8]}  frame={p['frame_id'][:8]}  v{p['space_version']}{reviewed}")
    print(f"\n{len(projections)} projection(s).")


def cmd_projection_show(args):
    from mkb.api import get_projection
    proj = get_projection(args.projection_id)
    if not proj:
        print(f"Projection {args.projection_id} not found.")
        sys.exit(1)
    _json_dump(proj)


def cmd_kg_extract(args):
    from mkb.api import extract_knowledge_graph

    result = extract_knowledge_graph(
        project_id=args.project_id,
        frame_id=args.frame_id,
        model=args.model,
        verbose=args.verbose,
        clear_existing=not args.no_clear_existing,
        clear_legacy_frame_sections=not args.keep_legacy_frame_graphs,
    )
    _json_dump(result)


def cmd_kg_clear(args):
    from mkb.api import clear_knowledge_graphs

    result = clear_knowledge_graphs(
        project_id=args.project_id,
        remove_legacy_frame_sections=not args.keep_legacy_frame_graphs,
    )
    _json_dump(result)


def cmd_kg_show(args):
    from mkb.api import get_knowledge_graph

    result = get_knowledge_graph(project_id=args.project_id)
    _json_dump(result)


# ── Feedback commands ────────────────────────────────────────────


def cmd_feedback(args):
    from mkb.api import list_feedback
    items = list_feedback(project_id=args.project_id, status=args.status)
    for fb in items:
        print(f"  {fb['feedback_id']}  {fb['status']:<12}  [{fb['category']}]  {fb['question'][:60]}")
    print(f"\n{len(items)} feedback item(s).")


def cmd_review_feedback(args):
    from mkb.api import review_feedback
    result = review_feedback(
        project_id=args.project_id,
        model=args.model,
        verbose=args.verbose,
    )
    _json_dump(result)


def cmd_resolve_feedback(args):
    from mkb.api import resolve_feedback
    result = resolve_feedback(
        feedback_id=args.feedback_id,
        status=args.status,
        notes=args.notes,
    )
    _json_dump(result)


# ── Projection review commands ──────────────────────────────────


def cmd_review_projections(args):
    from mkb.api import review_projections, review_projections_all
    if args.all:
        result = review_projections_all(
            space_id=args.space,
            model=args.model,
            verbose=args.verbose,
        )
    else:
        if not args.project_id:
            print("Error: --project-id is required unless --all is specified.")
            sys.exit(1)
        result = review_projections(
            space_id=args.space,
            project_id=args.project_id,
            model=args.model,
            verbose=args.verbose,
        )
    _json_dump(result)



# ── UI command ───────────────────────────────────────────────────


def cmd_ui(args):
    import subprocess
    import sys
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        "src/mkb/ui/app.py",
        "--server.port", str(args.port),
    ]
    subprocess.run(cmd)


def cmd_processed(args):
    from mkb.api import list_processed_assets
    processed = list_processed_assets(project_id=args.project_id, limit=args.limit)
    for p in processed:
        target = p["primary_relpath"] or p["s3_key"]
        print(
            f"  {p['asset_id']}  {p['processing_type']:<10}  "
            f"{p['output_format']:<8}  {target}"
        )
    print(f"\n{len(processed)} processed asset(s).")


def cmd_debug_link_processed(args):
    from mkb.api import link_manual_processed_data

    result = link_manual_processed_data(
        processed_dir=args.processed_dir,
        paper_dir=args.paper_dir,
        project_id=args.project_id,
        asset_id=args.asset_id,
        primary_file=args.primary_file,
        processing_type=args.processing_type,
        output_format=args.output_format,
    )
    _json_dump(result)


# ── Argument Parsing ─────────────────────────────────────────────


def main():
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
    p.add_argument("--field-descriptions", type=argparse.FileType("r"), default=None, help="JSON file with field descriptions")

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

    # ── Feedback subcommands ──
    p = sub.add_parser("feedback", help="List feedback items")
    p.add_argument("--project-id", "-p", default=None)
    p.add_argument("--status", default=None, help="Filter by status (OPEN, RESOLVED, DISMISSED, etc.)")

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


    # ── UI ──
    p = sub.add_parser("ui", help="Launch the Streamlit UI")
    p.add_argument("--port", type=int, default=8501)

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
        "frames": cmd_frames,
        "frame": cmd_frame,
        "processed": cmd_processed,
        "debug-link-processed": cmd_debug_link_processed,
        "extraction-history": cmd_extraction_history,
        "project-run": cmd_project_run,
        "projections": cmd_projections,
        "projection": cmd_projection_show,
        "kg-extract": cmd_kg_extract,
        "kg-clear": cmd_kg_clear,
        "kg-show": cmd_kg_show,
        "feedback": cmd_feedback,
        "review-feedback": cmd_review_feedback,
        "resolve-feedback": cmd_resolve_feedback,
        "review-projections": cmd_review_projections,
        "ui": cmd_ui,
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
