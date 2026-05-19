"""
Batch pipeline: raw PDFs → processed Markdown → QA-bench projection → YAML export.

Run from the repo root with the project's environment active, e.g.

    uv run python examples/batch_qa_bench_export.py \
        --papers data/papers \
        --space computational_materials_qa \
        --out data/exports/projections_yaml/qa_bench \
        --source markdown

The ``--source`` flag selects between the two projection workflows:

    frame    : 1) ingest 2) process (PDF→Markdown) 3) extract (Markdown→curated
               KnowledgeFrame) 4) project (Frame → space schema)
    markdown : 1) ingest 2) process 3) project (Markdown → space schema) — the
               extraction step is SKIPPED and the projection agent reads the
               processed Markdown directly via the ``get_project_markdown`` tool.

For a QA-bench (purpose == "qa_benchmark") space, the export step writes one
mat_agent_bench-ready YAML file per question under
``<out>/<capability>/<id>.yaml``. For any other space purpose, one
``<projection_id>.yaml`` (or ``.json``) file is written per projection.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mkb import api


def run(
    papers_dir: Path,
    space_id_or_name: str,
    out_dir: Path,
    source_type: str = "markdown",
    fmt: str = "yaml",
) -> None:
    # 1) Ingest every paper folder under ``papers_dir`` into MKB projects.
    print(f"[1/4] Syncing papers under {papers_dir} …")
    sync_result = api.sync(papers_dir)
    print(f"      → {sync_result}")

    # 2) Run PDF → Markdown across all pending assets.
    print("[2/4] Processing PDFs to Markdown …")
    proc_result = api.process()
    print(f"      → processed {proc_result.get('processed', 0)} asset(s)")

    # 3) Extract curated KnowledgeFrames (only needed for source=frame).
    if source_type == "frame":
        print("[3/4] Extracting knowledge frames …")
        ext_result = api.extract()
        print(f"      → {ext_result}")
    else:
        print("[3/4] Skipping extraction (source=markdown) …")

    # 4) Project each project into the chosen space.
    space = api.get_space(space_id_or_name)
    if not space:
        sys.exit(f"Space '{space_id_or_name}' not found. Create it first.")
    space_id = space["space_id"]
    print(
        f"[4/4] Projecting all projects into space '{space['name']}' "
        f"(purpose={space.get('purpose')}, source={source_type}) …"
    )

    projects = api.list_projects(limit=1000)
    completed, failed = 0, 0
    for proj in projects:
        pid = proj["project_id"]
        result = api.project(
            space_id=space_id,
            project_id=pid,
            source_type=source_type,
            verbose=False,
        )
        status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
        marker = "✓" if status == "completed" else "✗"
        print(f"      {marker} {proj.get('label', pid)[:60]:60s}  {status}")
        if status == "completed":
            completed += 1
        else:
            failed += 1
    print(f"      → completed={completed} failed={failed}")

    # 5) Export.
    print(f"[export] Writing {fmt.upper()} export to {out_dir} …")
    out_dir.mkdir(parents=True, exist_ok=True)
    export_result = api.export_space_projections(
        space_id, out_dir, format=fmt, overwrite=True,
    )
    if "error" in export_result:
        sys.exit(f"Export failed: {export_result['error']}")
    print(
        f"      → {len(export_result.get('files', []))} file(s), "
        f"{len(export_result.get('skipped', []))} skipped, "
        f"{len(export_result.get('warnings', []))} warning(s)"
    )
    for w in export_result.get("warnings", []):
        print(f"        ! {w}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--papers", type=Path, required=True, help="Root directory containing per-paper folders")
    parser.add_argument("--space", required=True, help="Space name or ID (purpose=qa_benchmark recommended)")
    parser.add_argument("--out", type=Path, required=True, help="Output directory for YAML/JSON")
    parser.add_argument("--source", choices=("frame", "markdown"), default="markdown",
                        help="Projection source — frame requires prior extraction; markdown skips it")
    parser.add_argument("--format", choices=("yaml", "json"), default="yaml",
                        help="Export format (qa_benchmark + yaml uses mat_agent_bench layout)")
    args = parser.parse_args()

    run(args.papers, args.space, args.out, source_type=args.source, fmt=args.format)


if __name__ == "__main__":
    main()
