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
  4. Extract knowledge frames via LLM
  5. Query the knowledge base
"""

from mkb import api

# ── 1. Setup ─────────────────────────────────────────────────────
# Create database tables (idempotent — safe to call multiple times)
api.setup()

# ── 2. Ingest ────────────────────────────────────────────────────
# Ingest a directory containing a paper and its supplementary files.
# All files in the directory become one "batch" (research package).
result = api.ingest(
    "./data/papers/smith2024_catalysis",
    label="Smith 2024 - Catalysis",
)
print(f"Ingested: {result}")
# → {'total': 4, 'ingested': 4, 'duplicates': 0, 'errors': 0}

# ── 3. Process ───────────────────────────────────────────────────
# Convert raw files to LLM-readable formats:
#   PDF → Markdown + images + tables
#   CSV/XLSX → Parquet
#   Images → JSON metadata
result = api.process()
print(f"Processed: {result}")

# Or process only one batch:
# api.process(batch_id="<batch-uuid>")

# ── 4. Extract ───────────────────────────────────────────────────
# Run LLM extraction on all batches that don't have a completed frame.
# The agent reads processed data and produces one knowledge frame per batch.
result = api.extract(verbose=True)
print(f"Extraction: {result}")

# Or extract one specific batch:
# api.extract(batch_id="<batch-uuid>")

# Override the model:
# api.extract(model="openai/gpt-4o")

# ── 5. Query Knowledge Frames ───────────────────────────────────

# List all frames
frames = api.list_frames()
for f in frames:
    print(f"  Batch {f['batch_id']}  status={f['status']}  checked={f['times_checked']}")
    print(f"    Summary: {f['extraction_summary']}")

# Get full frame for a specific batch
batches = api.list_batches()
if batches:
    batch_id = batches[0]["batch_id"]
    frame = api.get_frame(batch_id)

    if frame:
        content = frame["content"]

        # Paper metadata
        print(f"\nPaper: {content.get('paper', {}).get('title', 'N/A')}")

        # Materials studied
        for mat in content.get("materials", []):
            print(f"  Material: {mat['name']} ({mat.get('formula', '?')})")
            print(f"    Evidence level: {mat.get('evidence_level', '?')}")

        # Experimental data points
        for d in content.get("experimental_data", []):
            print(f"  Data: {d['property']} = {d['value']} {d.get('unit', '')}")
            print(f"    Method: {d.get('method', '?')}, Level: {d.get('evidence_level', '?')}")

        # Synthesis routes
        for route in content.get("synthesis_routes", []):
            inputs = " + ".join(route.get("inputs", []))
            outputs = " + ".join(route.get("outputs", []))
            print(f"  Synthesis: {inputs} → {outputs}")

        # Scientific statements
        for stmt in content.get("statements", []):
            print(f"  Statement (L{stmt.get('evidence_level', '?')}): {stmt['claim']}")

# ── 6. Other queries ─────────────────────────────────────────────

# List all batches
for b in api.list_batches():
    print(f"  {b['batch_id']}  {b['label']}  {b['asset_count']} files  frame: {b['frame_status']}")

# List assets in a batch
for a in api.list_assets(batch_id=batch_id):
    print(f"  {a['asset_id']}  {a['filename']}  ({a['mime_type']})")

# ── 7. Reset (for development) ──────────────────────────────────
# Uncomment to wipe everything and start fresh:
# api.reset_db()
