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
    result = extract(project_id=args.project_id, model=args.model, verbose=args.verbose)
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
        print(f"  {f['project_id']}  {f['status']:<12}  checked={f['times_checked']}  {f['extraction_summary'] or '':.60s}")
    print(f"\n{len(frames)} frame(s).")


def cmd_frame(args):
    from mkb.api import get_frame
    frame = get_frame(args.project_id)
    if not frame:
        print(f"No frame found for project {args.project_id}.")
        sys.exit(1)
    _json_dump(frame)


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
    }
    cmd_map[args.command](args)


if __name__ == "__main__":
    main()
