"""
Basic usage of the Materials Knowledge Base Python API.

Prerequisites:
  - Docker services running: `make up`
  - Package installed: `pip install -e ".[dev,processing]"`
  - .env configured with LLM credentials (see README.md)

This example walks through the full pipeline:
  1. Setup database
  2. Ingest files
  3. Process into LLM-readable formats
  4. Extract knowledge frames via LLM (with multi-pass review)
  5. Query the knowledge base
  6. Create a space and run projections
  7. Work with feedback
"""

from mkb import api

# ── 1. Setup ─────────────────────────────────────────────────────
# Create database tables (idempotent — safe to call multiple times)
api.setup()

# ── 2. Ingest ────────────────────────────────────────────────────
# Ingest a directory containing a paper and its supplementary files.
# All files in the directory become one "project" (research package).
result = api.ingest(
    "./data/papers/smith2024_catalysis",
    label="Smith 2024 - Catalysis",
)
print(f"Ingested: {result}")
# → {'total': 4, 'ingested': 4, 'duplicates': 0, 'errors': 0, 'project_id': '...'}

# Or sync all project subfolders at once:
# api.sync("./data/papers")

# ── 3. Process ───────────────────────────────────────────────────
# Convert raw files to LLM-readable formats:
#   PDF → Markdown + images + tables
#   CSV/XLSX → Parquet
#   Images → JSON metadata
result = api.process()
print(f"Processed: {result}")

# ── 4. Extract ───────────────────────────────────────────────────
# Run LLM extraction on all projects without a completed frame.
# The agent reads processed data and produces one knowledge frame per project.
#
# max_passes=1: initial extraction only
# max_passes=2+: initial extraction + review passes for refinement
result = api.extract(max_passes=2, verbose=True)
print(f"Extraction: {result}")

# Or extract one specific project:
# api.extract(project_id="<project-uuid>", max_passes=2)

# ── 5. Query Knowledge Frames ───────────────────────────────────

# List all frames
frames = api.list_frames()
for f in frames:
    print(f"  Project {f['project_id']}  status={f['status']}  v{f.get('extraction_version', 0)}")
    print(f"    Summary: {f['extraction_summary']}")

# Get full frame for a specific project
projects = api.list_projects()
if projects:
    project_id = projects[0]["project_id"]
    frame = api.get_frame(project_id)

    if frame:
        content = frame["content"]

        # Paper metadata (always present)
        paper = content.get("paper", {})
        print(f"\nPaper: {paper.get('title', 'N/A')}")
        print(f"Domain: {content.get('domain', 'N/A')}")

        # The rest of the keys are agent-decided — iterate dynamically
        for key, items in content.items():
            if key in ("paper", "domain"):
                continue
            if isinstance(items, list):
                print(f"\n{key} ({len(items)} items):")
                for item in items[:3]:  # show first 3
                    if isinstance(item, dict):
                        # Show evidence level and main descriptive field
                        ev = item.get("evidence_level", "?")
                        desc = next(
                            (item[k] for k in ("name", "claim", "property", "description", "method")
                             if k in item),
                            str(item)[:80]
                        )
                        print(f"  [L{ev}] {desc}")

        # View extraction history
        history = api.get_extraction_history(project_id)
        print(f"\nExtraction passes: {len(history)}")
        for h in history:
            print(f"  Pass {h['pass_number']} ({h['pass_type']})")

# ── 6. Spaces & Projections ─────────────────────────────────────

# Create a space (domain-specific extraction schema)
space_result = api.create_space(
    name="catalysis",
    domain="heterogeneous catalysis",
    extraction_schema={
        "catalysts": {
            "type": "list",
            "item_schema": {
                "name": {"type": "string", "required": True},
                "composition": {"type": "string", "required": True},
                "support": {"type": "string", "required": False},
                "surface_area_m2_g": {"type": "number", "required": False},
                "selectivity_percent": {"type": "number", "required": False},
                "conversion_percent": {"type": "number", "required": False},
            }
        },
        "reactions": {
            "type": "list",
            "item_schema": {
                "name": {"type": "string", "required": True},
                "reactants": {"type": "list", "required": True},
                "products": {"type": "list", "required": True},
                "temperature_C": {"type": "number", "required": False},
                "pressure_atm": {"type": "number", "required": False},
            }
        },
    },
    system_prompt="Extract catalyst materials and reactions from this paper.",
    field_descriptions={
        "catalysts": "All catalyst materials studied, including composition and performance metrics.",
        "reactions": "All chemical reactions described, with reactants, products, and conditions.",
    },
    description="Heterogeneous catalysis data extraction",
)
print(f"Space created: {space_result}")

# Run projection on a project's frame
# api.project(space_id=space_result["space_id"], project_id=project_id)

# Or project all completed frames:
# api.project_all(space_id=space_result["space_id"])

# List projections
# projections = api.list_projections(space_id=space_result["space_id"])

# ── 7. Feedback ──────────────────────────────────────────────────

# During projection, agents may flag unclear data.
# List open feedback:
# feedback = api.list_feedback(project_id=project_id, status="OPEN")

# Run feedback review (KB agent reviews and resolves feedback):
# api.review_feedback(project_id=project_id)

# Manually resolve feedback:
# api.resolve_feedback(feedback_id="...", status="RESOLVED", notes="Fixed")

# ── 8. Streamlit UI ──────────────────────────────────────────────

# Launch the web interface:
# python -m mkb ui

# ── 9. Other queries ─────────────────────────────────────────────

# List all projects
for p in api.list_projects():
    print(f"  {p['project_id']}  {p['label']}  {p['asset_count']} files  frame: {p['frame_status']}")

# List assets in a project
# for a in api.list_assets(project_id=project_id):
#     print(f"  {a['asset_id']}  {a['filename']}  ({a['mime_type']})")

# ── 10. Reset (for development) ─────────────────────────────────
# Uncomment to wipe everything and start fresh:
# api.reset_db()
