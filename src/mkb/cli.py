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
    from mkb.db.engine import SyncSessionLocal
    from mkb.db.models import ProcessedAsset, Asset

    with SyncSessionLocal() as session:
        query = session.query(ProcessedAsset).order_by(ProcessedAsset.created_at.desc())
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
    p_proc_list.add_argument("--limit", type=int, default=50)
    p_proc_list.set_defaults(func=cmd_processed_list)

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
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
