"""CLI entry point for ingestion and utilities."""

import argparse
import logging
import shutil
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── Commands ────────────────────────────────────────────────────

def cmd_ingest(args):
    from mkb.ingest.worker import ingest_directory

    stats = ingest_directory(args.directory, batch_label=args.label)
    print(f"Done: {stats}")


def cmd_list_assets(args):
    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import Asset

    with SyncSessionLocal() as session:
        assets = session.query(Asset).order_by(Asset.created_at.desc()).limit(args.limit).all()
        for a in assets:
            print(f"  {a.asset_id}  {a.status.value:<10}  {a.mime_type:<30}  {a.filename}")
        print(f"\nShowing {len(assets)} assets.")


def cmd_batches(args):
    """List all ingestion batches and their files."""
    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import Asset, BatchAsset, IngestionBatch

    with SyncSessionLocal() as session:
        batches = (
            session.query(IngestionBatch)
            .order_by(IngestionBatch.created_at.desc())
            .limit(args.limit)
            .all()
        )
        if not batches:
            print("No batches found.")
            return

        for b in batches:
            links = session.query(BatchAsset).filter_by(batch_id=b.batch_id).all()
            asset_ids = [l.asset_id for l in links]
            assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all() if asset_ids else []
            total_size = sum(a.size_bytes for a in assets)

            print(f"\n── Batch: {b.batch_id}")
            print(f"   Label:   {b.label or '(none)'}")
            print(f"   Created: {b.created_at}")
            print(f"   Files:   {len(assets)}  ({_human_size(total_size)})")
            for a in assets:
                print(f"     {a.asset_id}  {a.mime_type:<30}  {_human_size(a.size_bytes):>10}  {a.filename}")

        print(f"\nShowing {len(batches)} batches.")


def cmd_batch_info(args):
    """Show detailed info for a specific batch."""
    import uuid

    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import Asset, BatchAsset, IngestionBatch

    try:
        bid = uuid.UUID(args.batch_id)
    except ValueError:
        print(f"Error: invalid batch ID: {args.batch_id}")
        sys.exit(1)

    with SyncSessionLocal() as session:
        batch = session.query(IngestionBatch).filter_by(batch_id=bid).first()
        if not batch:
            print(f"Batch not found: {bid}")
            sys.exit(1)

        links = session.query(BatchAsset).filter_by(batch_id=bid).all()
        asset_ids = [l.asset_id for l in links]
        assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all() if asset_ids else []

        print(f"Batch ID:  {batch.batch_id}")
        print(f"Label:     {batch.label or '(none)'}")
        print(f"Created:   {batch.created_at}")
        print(f"Files:     {len(assets)}")
        print()
        for a in assets:
            print(f"  {a.asset_id}")
            print(f"    Filename:  {a.filename}")
            print(f"    MIME:      {a.mime_type}")
            print(f"    Size:      {_human_size(a.size_bytes)}")
            print(f"    SHA256:    {a.sha256}")
            print(f"    Status:    {a.status.value}")
            print(f"    S3:        s3://{a.s3_bucket}/{a.s3_key}")
            print(f"    Metadata:  {a.metadata_}")
            print()


def cmd_info(args):
    """Show detailed info for a single asset (by asset_id or sha256 prefix)."""
    import uuid

    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import Asset, BatchAsset, IngestionBatch

    with SyncSessionLocal() as session:
        asset = None
        # Try as UUID first
        try:
            aid = uuid.UUID(args.identifier)
            asset = session.query(Asset).filter_by(asset_id=aid).first()
        except ValueError:
            pass

        # Try as SHA256 prefix
        if not asset:
            prefix = args.identifier.lower()
            asset = session.query(Asset).filter(Asset.sha256.startswith(prefix)).first()

        if not asset:
            print(f"Asset not found: {args.identifier}")
            sys.exit(1)

        # Find which batches this asset belongs to
        links = session.query(BatchAsset).filter_by(asset_id=asset.asset_id).all()
        batch_ids = [l.batch_id for l in links]
        batches = session.query(IngestionBatch).filter(IngestionBatch.batch_id.in_(batch_ids)).all() if batch_ids else []

        print(f"Asset ID:   {asset.asset_id}")
        print(f"Filename:   {asset.filename}")
        print(f"MIME type:  {asset.mime_type}")
        print(f"Size:       {_human_size(asset.size_bytes)} ({asset.size_bytes} bytes)")
        print(f"SHA256:     {asset.sha256}")
        print(f"Status:     {asset.status.value}")
        print(f"S3 path:    s3://{asset.s3_bucket}/{asset.s3_key}")
        print(f"Created:    {asset.created_at}")
        print(f"Updated:    {asset.updated_at}")
        print(f"Metadata:   {asset.metadata_}")
        print(f"Embedding:  {'set' if asset.embedding else 'not set'}")
        if batches:
            print(f"Batches:")
            for b in batches:
                print(f"  {b.batch_id}  {b.label or '(none)'}  ({b.created_at})")
        else:
            print(f"Batches:    (not in any batch)")


def cmd_delete(args):
    """Delete a single asset from DB and MinIO."""
    import uuid

    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import Asset, BatchAsset
    from mkb.storage.s3 import delete_object

    try:
        aid = uuid.UUID(args.asset_id)
    except ValueError:
        print(f"Error: invalid asset ID: {args.asset_id}")
        sys.exit(1)

    with SyncSessionLocal() as session:
        asset = session.query(Asset).filter_by(asset_id=aid).first()
        if not asset:
            print(f"Asset not found: {aid}")
            sys.exit(1)

        if not args.yes:
            resp = input(f"Delete asset '{asset.filename}' ({asset.sha256[:12]})? [y/N] ")
            if resp.lower() != "y":
                print("Cancelled.")
                return

        # Remove from MinIO
        delete_object(asset.s3_bucket, asset.s3_key)
        # Remove batch links
        session.query(BatchAsset).filter_by(asset_id=aid).delete()
        # Remove asset row
        session.delete(asset)
        session.commit()
        print(f"Deleted: {asset.filename} ({aid})")


def cmd_delete_batch(args):
    """Delete a batch and all its assets."""
    import uuid

    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import Asset, BatchAsset, IngestionBatch
    from mkb.storage.s3 import delete_object

    try:
        bid = uuid.UUID(args.batch_id)
    except ValueError:
        print(f"Error: invalid batch ID: {args.batch_id}")
        sys.exit(1)

    with SyncSessionLocal() as session:
        batch = session.query(IngestionBatch).filter_by(batch_id=bid).first()
        if not batch:
            print(f"Batch not found: {bid}")
            sys.exit(1)

        links = session.query(BatchAsset).filter_by(batch_id=bid).all()
        asset_ids = [l.asset_id for l in links]
        assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all() if asset_ids else []

        if not args.yes:
            print(f"Batch: {batch.label or '(none)'} — {len(assets)} file(s):")
            for a in assets:
                print(f"  {a.filename}")
            resp = input("Delete this batch and all its assets? [y/N] ")
            if resp.lower() != "y":
                print("Cancelled.")
                return

        for a in assets:
            # Only delete from S3 if no other batch references this asset
            other_links = (
                session.query(BatchAsset)
                .filter(BatchAsset.asset_id == a.asset_id, BatchAsset.batch_id != bid)
                .count()
            )
            session.query(BatchAsset).filter_by(batch_id=bid, asset_id=a.asset_id).delete()
            if other_links == 0:
                delete_object(a.s3_bucket, a.s3_key)
                session.delete(a)

        session.delete(batch)
        session.commit()
        print(f"Deleted batch '{batch.label}' and {len(assets)} asset(s).")


def cmd_purge(args):
    """Wipe ALL data from DB and MinIO. For development only."""
    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import Asset, BatchAsset, IngestionBatch, KnowledgeEdge, KnowledgeNode
    from mkb.storage.s3 import delete_object

    if not args.yes:
        resp = input("PURGE ALL DATA from DB and MinIO? This cannot be undone. [y/N] ")
        if resp.lower() != "y":
            print("Cancelled.")
            return

    with SyncSessionLocal() as session:
        # Delete all S3 objects
        assets = session.query(Asset).all()
        for a in assets:
            try:
                delete_object(a.s3_bucket, a.s3_key)
            except Exception as e:
                logging.warning("Failed to delete s3://%s/%s: %s", a.s3_bucket, a.s3_key, e)

        # Clear all tables
        count_assets = session.query(Asset).count()
        count_batches = session.query(IngestionBatch).count()
        session.query(KnowledgeEdge).delete()
        session.query(KnowledgeNode).delete()
        session.query(BatchAsset).delete()
        session.query(Asset).delete()
        session.query(IngestionBatch).delete()
        session.commit()
        print(f"Purged {count_assets} assets, {count_batches} batches, and all knowledge nodes/edges.")


# ── Processing Commands ─────────────────────────────────────────

def cmd_process_asset(args):
    """Process a single asset."""
    import uuid
    from mkb.processors.coordinator import process_asset

    try:
        aid = uuid.UUID(args.asset_id)
    except ValueError:
        print(f"Error: invalid asset ID: {args.asset_id}")
        sys.exit(1)

    result = process_asset(aid)
    print(f"Status:           {result['status']}")
    print(f"Processing Type:  {result.get('processing_type', 'N/A')}")
    if result.get('processed_asset_id'):
        print(f"Processed Asset:  {result['processed_asset_id']}")
    if result.get('reason'):
        print(f"Reason:           {result['reason']}")


def cmd_process_all(args):
    """Process all unprocessed assets."""
    from mkb.processors.coordinator import process_all_pending

    limit = args.limit if args.limit > 0 else None
    stats = process_all_pending(limit=limit)

    print(f"\nProcessing Summary:")
    print(f"  Total:    {stats['total']}")
    print(f"  Success:  {stats['success']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Failed:   {stats['failed']}")

    if args.verbose:
        print(f"\nDetailed Results:")
        for result in stats['results']:
            status = result['status']
            asset_id = str(result['asset_id'])[:8]
            proc_type = result.get('processing_type', 'N/A')
            print(f"  {asset_id}... {status:<10} {proc_type}")


def cmd_processed_list(args):
    """List processed assets."""
    import uuid

    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import ProcessedAsset, Asset, BatchAsset

    with SyncSessionLocal() as session:
        query = session.query(ProcessedAsset).order_by(ProcessedAsset.created_at.desc())

        if args.batch_id:
            try:
                batch_id = uuid.UUID(args.batch_id)
            except ValueError:
                print(f"Error: invalid batch ID: {args.batch_id}")
                sys.exit(1)

            asset_ids = [
                row.asset_id for row in session.query(BatchAsset).filter_by(batch_id=batch_id).all()
            ]
            query = query.filter(ProcessedAsset.asset_id.in_(asset_ids)) if asset_ids else query.filter(False)

        if args.limit > 0:
            query = query.limit(args.limit)

        processed = query.all()
        if not processed:
            print("No processed assets found.")
            return

        print(f"{'UUID':<36} {'Type':<12} {'Format':<10} {'Size':<10} {'Raw Asset'}")
        print("-" * 100)
        for p in processed:
            asset = session.query(Asset).filter_by(asset_id=p.asset_id).first()
            filename = asset.filename if asset else "(unknown)"
            size_str = _human_size(p.size_bytes)
            asset_short = str(p.asset_id)[:8]
            print(f"{str(p.processed_asset_id):<36} {p.processing_type.value:<12} {p.output_format:<10} {size_str:<10} {asset_short}... {filename}")

        print(f"\nShowing {len(processed)} processed assets.")


def cmd_processed_batch_info(args):
    """Show processed-data details for a single ingestion batch."""
    import uuid
    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import ProcessedAsset, Asset, BatchAsset, IngestionBatch

    try:
        bid = uuid.UUID(args.batch_id)
    except ValueError:
        print(f"Error: invalid batch ID: {args.batch_id}")
        sys.exit(1)

    with SyncSessionLocal() as session:
        batch = session.query(IngestionBatch).filter_by(batch_id=bid).first()
        if not batch:
            print(f"Batch not found: {bid}")
            sys.exit(1)

        asset_ids = [row.asset_id for row in session.query(BatchAsset).filter_by(batch_id=bid).all()]
        assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all() if asset_ids else []
        processed_rows = (
            session.query(ProcessedAsset)
            .filter(ProcessedAsset.asset_id.in_(asset_ids))
            .order_by(ProcessedAsset.created_at.desc())
            .all()
            if asset_ids
            else []
        )

        processed_by_asset = {aid: [] for aid in asset_ids}
        for row in processed_rows:
            processed_by_asset.setdefault(row.asset_id, []).append(row)

        processed_asset_count = sum(1 for aid in asset_ids if processed_by_asset.get(aid))
        unprocessed_count = len(asset_ids) - processed_asset_count
        total_size = sum(row.size_bytes for row in processed_rows)

        print(f"Batch ID:           {batch.batch_id}")
        print(f"Label:              {batch.label or '(none)'}")
        print(f"Created:            {batch.created_at}")
        print(f"Raw assets:         {len(asset_ids)}")
        print(f"Assets processed:   {processed_asset_count}")
        print(f"Assets pending:     {unprocessed_count}")
        print(f"Processed outputs:  {len(processed_rows)}")
        print(f"Total output size:  {_human_size(total_size)} ({total_size} bytes)")

        if not assets:
            print("\nNo raw assets are linked to this batch.")
            return

        print("\nPer-asset details:")
        for asset in assets:
            rows = processed_by_asset.get(asset.asset_id, [])
            print(f"\n  Raw Asset:  {asset.asset_id}")
            print(f"    Filename:  {asset.filename}")
            print(f"    MIME:      {asset.mime_type}")
            print(f"    Processed: {len(rows)} output(s)")

            if not rows:
                print("    Status:    pending / no processed outputs")
                continue

            for row in rows:
                print(f"    - {row.processed_asset_id}  [{row.processing_type.value}]  {row.output_format}  {_human_size(row.size_bytes)}")
                print(f"      s3://{row.s3_bucket}/{row.s3_key}")


def cmd_processed_info(args):
    """Show detailed info for a processed asset."""
    import uuid
    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import ProcessedAsset, Asset

    try:
        pid = uuid.UUID(args.processed_asset_id)
    except ValueError:
        print(f"Error: invalid processed asset ID: {args.processed_asset_id}")
        sys.exit(1)

    with SyncSessionLocal() as session:
        processed = session.query(ProcessedAsset).filter_by(processed_asset_id=pid).first()
        if not processed:
            print(f"Processed asset not found: {pid}")
            sys.exit(1)

        asset = session.query(Asset).filter_by(asset_id=processed.asset_id).first()

        print(f"Processed Asset ID : {processed.processed_asset_id}")
        print(f"Raw Asset ID       : {processed.asset_id}")
        if asset:
            print(f"Raw Filename       : {asset.filename}")
        print(f"Processing Type    : {processed.processing_type.value}")
        print(f"Output Format      : {processed.output_format}")
        print(f"Size               : {_human_size(processed.size_bytes)} ({processed.size_bytes} bytes)")
        print(f"SHA256             : {processed.sha256}")
        print(f"S3 Path            : s3://{processed.s3_bucket}/{processed.s3_key}")
        print(f"Raw Asset Hash     : {processed.raw_asset_hash}")
        print(f"Created            : {processed.created_at}")
        print(f"Updated            : {processed.updated_at}")
        print(f"Metadata           : {processed.conversion_metadata}")


def _clear_processing_metadata(asset) -> None:
    metadata = dict(asset.metadata_ or {})
    if "processing" in metadata:
        metadata.pop("processing", None)
        asset.metadata_ = metadata


def _delete_processed_rows(session, processed_rows) -> tuple[int, int, int]:
    from mkb.db.models import Asset, ProcessingLog
    from mkb.storage.s3 import delete_prefix

    deleted_s3_objects = 0
    deleted_local_dirs = 0
    affected_asset_ids = set()

    for row in processed_rows:
        affected_asset_ids.add(row.asset_id)

        prefix = row.s3_key.rsplit("/", 1)[0]
        deleted_s3_objects += delete_prefix(row.s3_bucket, prefix)

        local_dir = (row.conversion_metadata or {}).get("local_dir")
        if local_dir:
            local_path = shutil.rmtree(local_dir, ignore_errors=True)
            deleted_local_dirs += 1

        session.delete(row)

    if affected_asset_ids:
        session.query(ProcessingLog).filter(ProcessingLog.asset_id.in_(affected_asset_ids)).delete(
            synchronize_session=False
        )
        assets = session.query(Asset).filter(Asset.asset_id.in_(affected_asset_ids)).all()
        for asset in assets:
            _clear_processing_metadata(asset)

    return len(processed_rows), deleted_s3_objects, deleted_local_dirs


def cmd_clear_processed(args):
    """Clear processed outputs only, keeping raw assets intact."""
    import uuid

    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import BatchAsset, ProcessedAsset

    with SyncSessionLocal() as session:
        query = session.query(ProcessedAsset)
        scope_label = None

        if args.all:
            scope_label = "all processed data"
        elif args.asset_id:
            try:
                asset_id = uuid.UUID(args.asset_id)
            except ValueError:
                print(f"Error: invalid asset ID: {args.asset_id}")
                sys.exit(1)
            query = query.filter_by(asset_id=asset_id)
            scope_label = f"processed data for asset {asset_id}"
        elif args.batch_id:
            try:
                batch_id = uuid.UUID(args.batch_id)
            except ValueError:
                print(f"Error: invalid batch ID: {args.batch_id}")
                sys.exit(1)
            asset_ids = [
                row.asset_id for row in session.query(BatchAsset).filter_by(batch_id=batch_id).all()
            ]
            query = query.filter(ProcessedAsset.asset_id.in_(asset_ids)) if asset_ids else query.filter(False)
            scope_label = f"processed data for batch {batch_id}"
        else:
            print("Error: one of --all, --asset-id, or --batch-id is required")
            sys.exit(1)

        processed_rows = query.all()
        if not processed_rows:
            print(f"No processed rows found for {scope_label}.")
            return

        if not args.yes:
            resp = input(f"Delete {len(processed_rows)} processed row(s) from {scope_label}? [y/N] ")
            if resp.lower() != "y":
                print("Cancelled.")
                return

        deleted_rows, deleted_s3_objects, deleted_local_dirs = _delete_processed_rows(
            session, processed_rows
        )
        session.commit()

        print(f"Deleted processed rows : {deleted_rows}")
        print(f"Deleted S3 objects     : {deleted_s3_objects}")
        print(f"Deleted local dirs     : {deleted_local_dirs}")
        print("Raw assets were preserved.")


# ── Extraction Commands ─────────────────────────────────────────

def cmd_extract_batch(args):
    """Run knowledge extraction agent on a single batch."""
    import uuid
    from mkb.agents.extraction import run_extraction

    try:
        bid = uuid.UUID(args.batch_id)
    except ValueError:
        print(f"Error: invalid batch ID: {args.batch_id}")
        sys.exit(1)

    print(f"Starting knowledge extraction for batch {bid} ...")
    result = run_extraction(bid, model=args.model, verbose=args.verbose)

    print(f"\nExtraction Result:")
    print(f"  Status:         {result['status']}")
    if result.get("entities_created") is not None:
        print(f"  Entities:       {result['entities_created']}")
    if result.get("relationships_created") is not None:
        print(f"  Relationships:  {result['relationships_created']}")
    if result.get("message"):
        print(f"  Message:        {result['message']}")
    if result.get("agent_summary"):
        print(f"\nAgent summary:\n{result['agent_summary'][:2000]}")


def cmd_extract_all(args):
    """Run extraction on all pending/failed batches."""
    from mkb.agents.extraction import run_extraction_all

    limit = args.limit if args.limit > 0 else None
    print("Starting extraction on pending batches ...")
    stats = run_extraction_all(limit=limit, model=args.model, verbose=args.verbose)

    print(f"\nExtraction Summary:")
    print(f"  Total batches:       {stats['total_batches']}")
    print(f"  Completed:           {stats['completed']}")
    print(f"  Failed:              {stats['failed']}")
    print(f"  Total entities:      {stats['total_entities']}")
    print(f"  Total relationships: {stats['total_relationships']}")


def cmd_knowledge_list(args):
    """List extracted knowledge entities."""
    import uuid

    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import KnowledgeNode

    with SyncSessionLocal() as session:
        q = session.query(KnowledgeNode).order_by(KnowledgeNode.created_at.desc())

        if args.batch_id:
            try:
                bid = uuid.UUID(args.batch_id)
            except ValueError:
                print(f"Error: invalid batch ID: {args.batch_id}")
                sys.exit(1)
            q = q.filter_by(source_batch_id=bid)

        if args.type:
            q = q.filter_by(entity_type=args.type)

        nodes = q.limit(args.limit).all()
        if not nodes:
            print("No knowledge entities found.")
            return

        print(f"{'Node ID':<36}  {'Type':<20}  {'Label'}")
        print("-" * 100)
        for n in nodes:
            props_summary = ""
            if n.properties:
                items = list(n.properties.items())[:3]
                props_summary = "  " + ", ".join(f"{k}={v}" for k, v in items)
            print(f"{str(n.node_id):<36}  {n.entity_type:<20}  {n.label}{props_summary}")

        print(f"\nShowing {len(nodes)} entities.")


# ── UI Command ──────────────────────────────────────────────────

def cmd_ui(args):
    """Launch the Streamlit knowledge-graph explorer."""
    import subprocess
    from pathlib import Path

    app_path = Path(__file__).parent / "ui" / "app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(app_path),
        "--server.port", str(args.port),
        "--server.headless", "true",
    ]
    print(f"Starting Knowledge Graph UI on http://localhost:{args.port}")
    subprocess.run(cmd)


def cmd_knowledge_graph(args):
    """Show entities + relationships for a batch."""
    import uuid

    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import KnowledgeEdge, KnowledgeNode

    try:
        bid = uuid.UUID(args.batch_id)
    except ValueError:
        print(f"Error: invalid batch ID: {args.batch_id}")
        sys.exit(1)

    with SyncSessionLocal() as session:
        nodes = session.query(KnowledgeNode).filter_by(source_batch_id=bid).all()
        if not nodes:
            print(f"No entities found for batch {bid}.")
            return

        node_map = {n.node_id: n for n in nodes}
        node_ids = list(node_map.keys())

        edges = (
            session.query(KnowledgeEdge)
            .filter(KnowledgeEdge.source_node_id.in_(node_ids))
            .all()
        )

        print(f"=== Knowledge Graph for batch {bid} ===\n")
        print(f"Entities ({len(nodes)}):")
        for n in nodes:
            props_str = ""
            if n.properties:
                items = list(n.properties.items())[:4]
                props_str = " | " + ", ".join(f"{k}={v}" for k, v in items)
            print(f"  [{n.entity_type}] {n.label}{props_str}")

        print(f"\nRelationships ({len(edges)}):")
        for e in edges:
            src = node_map.get(e.source_node_id)
            tgt = node_map.get(e.target_node_id)
            src_label = src.label if src else str(e.source_node_id)[:8]
            tgt_label = tgt.label if tgt else str(e.target_node_id)[:8]
            props_str = ""
            if e.properties:
                items = list(e.properties.items())[:3]
                props_str = "  (" + ", ".join(f"{k}={v}" for k, v in items) + ")"
            print(f"  {src_label} --[{e.relation_type}]--> {tgt_label}{props_str}")


def cmd_clear_knowledge(args):
    """Delete extracted knowledge (nodes + edges) and reset extraction status."""
    import uuid

    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import ExtractionStatus, KnowledgeEdge, KnowledgeNode, IngestionBatch

    with SyncSessionLocal() as session:
        if args.batch_id:
            try:
                bid = uuid.UUID(args.batch_id)
            except ValueError:
                print(f"Error: invalid batch ID: {args.batch_id}")
                sys.exit(1)

            nodes = session.query(KnowledgeNode).filter_by(source_batch_id=bid).all()
            node_ids = [n.node_id for n in nodes]
            scope_label = f"batch {bid}"
        elif args.all:
            nodes = session.query(KnowledgeNode).all()
            node_ids = [n.node_id for n in nodes]
            scope_label = "ALL batches"
        else:
            print("Error: one of --all or --batch-id is required")
            sys.exit(1)

        if not nodes:
            print(f"No knowledge data found for {scope_label}.")
            return

        # Count edges
        edge_count = 0
        if node_ids:
            edge_count = (
                session.query(KnowledgeEdge)
                .filter(
                    (KnowledgeEdge.source_node_id.in_(node_ids))
                    | (KnowledgeEdge.target_node_id.in_(node_ids))
                )
                .count()
            )

        if not args.yes:
            resp = input(
                f"Delete {len(nodes)} entities and ~{edge_count} relationships "
                f"from {scope_label}? [y/N] "
            )
            if resp.lower() != "y":
                print("Cancelled.")
                return

        # Delete edges first
        if node_ids:
            session.query(KnowledgeEdge).filter(
                (KnowledgeEdge.source_node_id.in_(node_ids))
                | (KnowledgeEdge.target_node_id.in_(node_ids))
            ).delete(synchronize_session=False)

        # Delete nodes
        if args.batch_id:
            session.query(KnowledgeNode).filter_by(source_batch_id=bid).delete(
                synchronize_session=False
            )
            # Reset extraction status
            batch = session.query(IngestionBatch).filter_by(batch_id=bid).first()
            if batch:
                batch.extraction_status = ExtractionStatus.PENDING
                batch.extraction_metadata = {}
        elif args.all:
            session.query(KnowledgeEdge).delete(synchronize_session=False)
            session.query(KnowledgeNode).delete(synchronize_session=False)
            # Reset all batches
            session.query(IngestionBatch).update(
                {
                    IngestionBatch.extraction_status: ExtractionStatus.PENDING,
                    IngestionBatch.extraction_metadata: {},
                },
                synchronize_session=False,
            )

        session.commit()
        print(f"Deleted {len(nodes)} entities and {edge_count} relationships from {scope_label}.")
        print("Extraction status reset to PENDING.")


# ── CLI Main ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="mkb", description="mat-know-base CLI")
    sub = parser.add_subparsers(dest="command")

    # ── ingest ──────────────────────────────────────────────────
    p_ingest = sub.add_parser("ingest", help="Ingest files from a local directory")
    p_ingest.add_argument("directory", help="Path to the directory to ingest")
    p_ingest.add_argument("--label", default=None, help="Batch label")
    p_ingest.set_defaults(func=cmd_ingest)

    # ── list ────────────────────────────────────────────────────
    p_list = sub.add_parser("list", help="List stored assets")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list_assets)

    # ── batches ─────────────────────────────────────────────────
    p_batches = sub.add_parser("batches", help="List batches with their grouped files")
    p_batches.add_argument("--limit", type=int, default=50)
    p_batches.set_defaults(func=cmd_batches)

    # ── batch-info ──────────────────────────────────────────────
    p_binfo = sub.add_parser("batch-info", help="Show details of a specific batch")
    p_binfo.add_argument("batch_id", help="Batch UUID")
    p_binfo.set_defaults(func=cmd_batch_info)

    # ── info ────────────────────────────────────────────────────
    p_info = sub.add_parser("info", help="Show detailed info for an asset")
    p_info.add_argument("identifier", help="Asset UUID or SHA256 prefix")
    p_info.set_defaults(func=cmd_info)

    # ── delete ──────────────────────────────────────────────────
    p_del = sub.add_parser("delete", help="Delete a single asset (DB + S3)")
    p_del.add_argument("asset_id", help="Asset UUID")
    p_del.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_del.set_defaults(func=cmd_delete)

    # ── delete-batch ────────────────────────────────────────────
    p_delbatch = sub.add_parser("delete-batch", help="Delete a batch and all its assets")
    p_delbatch.add_argument("batch_id", help="Batch UUID")
    p_delbatch.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_delbatch.set_defaults(func=cmd_delete_batch)

    # ── purge ───────────────────────────────────────────────────
    p_purge = sub.add_parser("purge", help="Wipe ALL data (DB + S3). Dev only.")
    p_purge.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_purge.set_defaults(func=cmd_purge)
    # ── process-asset ───────────────────────────────────────
    p_proc_asset = sub.add_parser("process-asset", help="Process a single asset")
    p_proc_asset.add_argument("asset_id", help="Asset UUID")
    p_proc_asset.set_defaults(func=cmd_process_asset)

    # ── process-all ─────────────────────────────────────────
    p_proc_all = sub.add_parser("process-all", help="Process all unprocessed assets")
    p_proc_all.add_argument("--limit", type=int, default=0, help="Max assets to process (0=all)")
    p_proc_all.add_argument("-v", "--verbose", action="store_true", help="Show detailed results")
    p_proc_all.set_defaults(func=cmd_process_all)

    # ── processed-list ──────────────────────────────────────
    p_proc_list = sub.add_parser("processed-list", help="List processed assets")
    p_proc_list.add_argument("--batch-id", default=None, help="Filter by batch UUID")
    p_proc_list.add_argument("--limit", type=int, default=50)
    p_proc_list.set_defaults(func=cmd_processed_list)

    # ── processed-batch-info ────────────────────────────────
    p_proc_batch_info = sub.add_parser(
        "processed-batch-info", help="Show processed-data details for a batch"
    )
    p_proc_batch_info.add_argument("batch_id", help="Batch UUID")
    p_proc_batch_info.set_defaults(func=cmd_processed_batch_info)

    # ── processed-info ─────────────────────────────────────
    p_proc_info = sub.add_parser("processed-info", help="Show details of a processed asset")
    p_proc_info.add_argument("processed_asset_id", help="Processed Asset UUID")
    p_proc_info.set_defaults(func=cmd_processed_info)

    # ── clear-processed ────────────────────────────────────
    p_clear_processed = sub.add_parser(
        "clear-processed",
        help="Delete processed outputs only (DB + local processed dir + processed S3 objects)",
    )
    scope = p_clear_processed.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="Delete all processed outputs")
    scope.add_argument("--asset-id", help="Delete processed outputs for one raw asset UUID")
    scope.add_argument("--batch-id", help="Delete processed outputs for all assets in a batch UUID")
    p_clear_processed.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_clear_processed.set_defaults(func=cmd_clear_processed)

    # ── extract-batch ───────────────────────────────────────
    p_extract = sub.add_parser("extract-batch", help="Run knowledge extraction on one batch")
    p_extract.add_argument("batch_id", help="Batch UUID")
    p_extract.add_argument("--model", default=None, help="Override LLM model name")
    p_extract.add_argument("-v", "--verbose", action="store_true", help="Log agent actions")
    p_extract.set_defaults(func=cmd_extract_batch)

    # ── extract-all ─────────────────────────────────────────
    p_extract_all = sub.add_parser("extract-all", help="Extract knowledge from all pending batches")
    p_extract_all.add_argument("--limit", type=int, default=0, help="Max batches (0=all)")
    p_extract_all.add_argument("--model", default=None, help="Override LLM model name")
    p_extract_all.add_argument("-v", "--verbose", action="store_true", help="Log agent actions")
    p_extract_all.set_defaults(func=cmd_extract_all)

    # ── knowledge-list ──────────────────────────────────────
    p_kg_list = sub.add_parser("knowledge-list", help="List extracted knowledge entities")
    p_kg_list.add_argument("--batch-id", default=None, help="Filter by batch UUID")
    p_kg_list.add_argument("--type", default=None, help="Filter by entity type")
    p_kg_list.add_argument("--limit", type=int, default=50)
    p_kg_list.set_defaults(func=cmd_knowledge_list)

    # ── knowledge-graph ─────────────────────────────────────
    p_kg_graph = sub.add_parser("knowledge-graph", help="Show entities + relationships for a batch")
    p_kg_graph.add_argument("batch_id", help="Batch UUID")
    p_kg_graph.set_defaults(func=cmd_knowledge_graph)

    # ── clear-knowledge ─────────────────────────────────────
    p_clear_kg = sub.add_parser(
        "clear-knowledge",
        help="Delete extracted knowledge (nodes + edges) and reset extraction status",
    )
    scope_kg = p_clear_kg.add_mutually_exclusive_group(required=True)
    scope_kg.add_argument("--all", action="store_true", help="Delete ALL knowledge data")
    scope_kg.add_argument("--batch-id", help="Delete knowledge for one batch UUID")
    p_clear_kg.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_clear_kg.set_defaults(func=cmd_clear_knowledge)

    # ── ui ──────────────────────────────────────────────────
    p_ui = sub.add_parser("ui", help="Launch the Streamlit knowledge-graph explorer")
    p_ui.add_argument("--port", type=int, default=8501, help="Port (default 8501)")
    p_ui.set_defaults(func=cmd_ui)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
