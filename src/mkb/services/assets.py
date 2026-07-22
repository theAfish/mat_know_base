"""Assets API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    Path,
    settings,
    uuid,
    _choose_asset_for_manual_output,
    _inspect_manual_processed_dir,
    _matches_search_tokens,
    _normalize_search_query,
)

from mkb.ports import Database, ObjectStore


def process(
    project_id: str | uuid.UUID | None = None,
    progress_callback=None,
    *,
    database: Database,
    object_store: ObjectStore,
    processed_bucket: str,
) -> dict:
    """Process assets. If project_id is given, process only that project's assets.
    Otherwise process all pending assets.

    Returns a summary dict.
    """
    from mkb.processors.coordinator import process_all_pending, process_asset

    if project_id is not None:
        pid = uuid.UUID(str(project_id))
        from mkb.db.models import ProjectAsset
        with database.session() as session:
            links = session.query(ProjectAsset).filter_by(project_id=pid).all()
            asset_ids = [link.asset_id for link in links]

        results = []
        for aid in asset_ids:
            try:
                if progress_callback:
                    progress_callback({"message": f"Starting asset {len(results) + 1}/{len(asset_ids)}", "asset_id": str(aid)})
                r = process_asset(
                    aid,
                    database=database,
                    object_store=object_store,
                    processed_bucket=processed_bucket,
                    progress_callback=progress_callback,
                )
                results.append(r)
            except Exception as exc:
                results.append({"asset_id": str(aid), "error": str(exc)})
        return {"project_id": str(pid), "assets_processed": len(results), "results": results}

    return process_all_pending(
        database=database,
        object_store=object_store,
        processed_bucket=processed_bucket,
        progress_callback=progress_callback,
    )


# ── Extraction ───────────────────────────────────────────────────

def list_processed_assets(
    project_id: str | uuid.UUID | None = None,
    limit: int = 100,
    *,
    database: Database,
) -> list[dict]:
    """List processed outputs, optionally filtered by project."""
    from mkb.db.models import Asset, ProcessedAsset, ProjectAsset

    with database.session() as session:
        q = session.query(ProcessedAsset).order_by(ProcessedAsset.created_at.desc())
        if project_id is not None:
            pid = uuid.UUID(str(project_id))
            links = session.query(ProjectAsset).filter_by(project_id=pid).all()
            asset_ids = [link.asset_id for link in links]
            if not asset_ids:
                return []
            q = q.filter(ProcessedAsset.asset_id.in_(asset_ids))

        rows = q.limit(limit).all()
        result = []
        for row in rows:
            asset = session.query(Asset).filter_by(asset_id=row.asset_id).first()
            meta = row.conversion_metadata or {}
            result.append({
                "processed_asset_id": str(row.processed_asset_id),
                "asset_id": str(row.asset_id),
                "filename": asset.filename if asset else None,
                "processing_type": row.processing_type.value,
                "output_format": row.output_format,
                "s3_key": row.s3_key,
                "local_dir": meta.get("local_dir"),
                "primary_relpath": meta.get("primary_relpath"),
                "artifact_count": meta.get("artifact_count", 0),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })
        return result

def link_manual_processed_data(
    processed_dir: str | Path,
    paper_dir: str | Path | None = None,
    project_id: str | uuid.UUID | None = None,
    asset_id: str | uuid.UUID | None = None,
    primary_file: str | None = None,
    processing_type: str | None = None,
    output_format: str | None = None,
    *,
    database: Database,
    object_store: ObjectStore,
    processed_bucket: str,
) -> dict:
    """Attach a handmade processed-output folder to an existing project asset.

    This is intended for debugging or backfilling local outputs that were created
    outside the normal processing pipeline.
    """
    from mkb.db.models import Asset, ProcessedAsset, ProcessingLog, ProcessingType, ProjectAsset, ResearchProject

    bundle = _inspect_manual_processed_dir(processed_dir, primary_file=primary_file)
    paper_path = Path(paper_dir).resolve() if paper_dir is not None else None

    if processing_type:
        proc_type = ProcessingType(processing_type.upper())
    else:
        proc_type = bundle["processing_type"]
    out_format = output_format or bundle["output_format"]

    with database.session() as session:
        project = None
        if project_id is not None:
            pid = uuid.UUID(str(project_id))
            project = session.query(ResearchProject).filter_by(project_id=pid).first()
        elif paper_path is not None:
            project = session.query(ResearchProject).filter_by(source_path=str(paper_path)).first()
            if project is None and paper_path.is_dir():
                from mkb.services.compatibility_resources import content_operations

                ingest_result = content_operations().ingest(
                    paper_path,
                    label=paper_path.name,
                )
                pid = uuid.UUID(ingest_result["project_id"])
                project = session.query(ResearchProject).filter_by(project_id=pid).first()
        elif asset_id is not None:
            # Look up the owning project via ProjectAsset link
            aid = uuid.UUID(str(asset_id))
            link = session.query(ProjectAsset).filter_by(asset_id=aid).first()
            if link is not None:
                project = (
                    session.query(ResearchProject)
                    .filter_by(project_id=link.project_id)
                    .first()
                )

        if not project:
            raise ValueError("Could not find a target project. Provide --paper-dir or --project-id.")

        if asset_id is not None:
            target_asset = session.query(Asset).filter_by(asset_id=uuid.UUID(str(asset_id))).first()
        else:
            links = session.query(ProjectAsset).filter_by(project_id=project.project_id).all()
            asset_ids = [link.asset_id for link in links]
            assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all() if asset_ids else []
            target_asset = _choose_asset_for_manual_output(assets, bundle["primary_name"])

        if not target_asset:
            raise ValueError(
                "No raw asset found for the target project. Ingest the paper folder first or pass --asset-id."
            )

        link = session.query(ProjectAsset).filter_by(
            project_id=project.project_id,
            asset_id=target_asset.asset_id,
        ).first()
        if not link:
            session.add(ProjectAsset(project_id=project.project_id, asset_id=target_asset.asset_id))

        s3_key = f"{project.project_id}/{target_asset.asset_id}/{bundle['primary_relpath']}"

        # Mirror the bundle into the canonical processed-local-root so it survives
        # after any caller-supplied temp directory is cleaned up. The local cache
        # is used by the idempotency check and by downstream readers.
        import shutil

        canonical_root = (
            Path(settings.processed_local_root)
            / str(project.project_id)
            / str(target_asset.asset_id)
        )
        bundle_root = Path(bundle["local_dir"]).resolve()
        if bundle_root != canonical_root.resolve():
            canonical_root.mkdir(parents=True, exist_ok=True)
            for relpath in [bundle["primary_relpath"], *bundle["artifact_files"]]:
                src = bundle_root / relpath
                dst = canonical_root / relpath
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            bundle["local_dir"] = str(canonical_root)
            bundle_root = canonical_root

        # Upload primary file + artifacts through the configured object store so
        # downstream consumers can fetch the bundle independently of MinIO/S3.
        object_store.put_bytes(
            processed_bucket,
            s3_key,
            (bundle_root / bundle["primary_relpath"]).read_bytes(),
        )
        for relpath in bundle["artifact_files"]:
            artifact_key = f"{project.project_id}/{target_asset.asset_id}/{relpath}"
            object_store.put_bytes(
                processed_bucket, artifact_key, (bundle_root / relpath).read_bytes()
            )

        metadata = {
            "project_id": str(project.project_id),
            "local_dir": bundle["local_dir"],
            "primary_relpath": bundle["primary_relpath"],
            "artifact_files": bundle["artifact_files"],
            "artifact_count": len(bundle["artifact_files"]),
            "linked_via": "debug_manual_link",
            "paper_dir": str(paper_path) if paper_path is not None else None,
        }

        existing = (
            session.query(ProcessedAsset)
            .filter_by(asset_id=target_asset.asset_id, processing_type=proc_type)
            .order_by(ProcessedAsset.created_at.desc())
            .first()
        )

        if existing:
            existing.output_format = out_format
            existing.s3_bucket = processed_bucket
            existing.s3_key = s3_key
            existing.sha256 = bundle["sha256"]
            existing.size_bytes = bundle["size_bytes"]
            existing.conversion_metadata = metadata
            existing.raw_asset_hash = target_asset.sha256
            processed_asset = existing
            action = "updated"
        else:
            processed_asset = ProcessedAsset(
                processed_asset_id=uuid.uuid4(),
                asset_id=target_asset.asset_id,
                processing_type=proc_type,
                output_format=out_format,
                s3_bucket=processed_bucket,
                s3_key=s3_key,
                sha256=bundle["sha256"],
                size_bytes=bundle["size_bytes"],
                conversion_metadata=metadata,
                raw_asset_hash=target_asset.sha256,
            )
            session.add(processed_asset)
            action = "created"

        asset_meta = dict(target_asset.metadata_ or {})
        processing_meta = dict(asset_meta.get("processing") or {})
        processing_meta.update(
            {
                "last_status": "SUCCESS",
                "last_processing_type": proc_type.value,
                "last_output_format": out_format,
                "last_processed_asset_id": str(processed_asset.processed_asset_id),
                "last_processed_local_dir": bundle["local_dir"],
                "source": "manual_debug_link",
            }
        )
        asset_meta["processing"] = processing_meta
        target_asset.metadata_ = asset_meta

        session.add(
            ProcessingLog(
                log_id=uuid.uuid4(),
                asset_id=target_asset.asset_id,
                processing_type=proc_type,
                status="SUCCESS",
                processed_asset_id=processed_asset.processed_asset_id,
                details={
                    "debug": True,
                    "action": action,
                    "primary_relpath": bundle["primary_relpath"],
                    "artifact_count": len(bundle["artifact_files"]),
                },
            )
        )
        session.commit()

        return {
            "status": action,
            "project_id": str(project.project_id),
            "asset_id": str(target_asset.asset_id),
            "processed_asset_id": str(processed_asset.processed_asset_id),
            "processing_type": proc_type.value,
            "output_format": out_format,
            "local_dir": bundle["local_dir"],
            "primary_relpath": bundle["primary_relpath"],
            "artifact_files": bundle["artifact_files"],
        }

def list_assets(
    project_id: str | uuid.UUID | None = None,
    limit: int = 100,
    *,
    database: Database,
) -> list[dict]:
    """List assets, optionally filtered by project."""
    from mkb.db.models import Asset, ProjectAsset

    with database.session() as session:
        if project_id is not None:
            pid = uuid.UUID(str(project_id))
            links = session.query(ProjectAsset).filter_by(project_id=pid).all()
            asset_ids = [link.asset_id for link in links]
            if not asset_ids:
                return []
            assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all()
        else:
            assets = (
                session.query(Asset)
                .order_by(Asset.created_at.desc())
                .limit(limit)
                .all()
            )
        return [
            {
                "asset_id": str(a.asset_id),
                "filename": a.filename,
                "mime_type": a.mime_type,
                "size_bytes": a.size_bytes,
                "status": a.status.value,
            }
            for a in assets
        ]

def search_library(
    query: str,
    limit: int = 25,
    project_id: str | uuid.UUID | None = None,
    *,
    database: Database,
) -> dict:
    """Search projects and assets by keyword.

    Every keyword token must match somewhere in the target record. Projects are
    searched by label and source path. Assets are searched by filename, MIME
    type, and selected metadata fields.
    """
    from sqlalchemy import and_, or_

    from mkb.db.models import Asset, ProjectAsset, ResearchProject

    tokens = [token.lower() for token in _normalize_search_query(query)]
    if not tokens:
        return {
            "query": query,
            "tokens": [],
            "project_id": str(project_id) if project_id is not None else None,
            "projects": [],
            "assets": [],
            "total": 0,
        }

    pid = uuid.UUID(str(project_id)) if project_id is not None else None

    with database.session() as session:
        project_filters = [
            or_(
                ResearchProject.label.ilike(f"%{token}%"),
                ResearchProject.source_path.ilike(f"%{token}%"),
            )
            for token in tokens
        ]
        project_query = session.query(ResearchProject)
        if pid is not None:
            project_query = project_query.filter(ResearchProject.project_id == pid)
        project_rows = (
            project_query
            .filter(and_(*project_filters))
            .order_by(ResearchProject.created_at.desc())
            .limit(limit)
            .all()
        )

        asset_filters = [
            or_(
                Asset.filename.ilike(f"%{token}%"),
                Asset.mime_type.ilike(f"%{token}%"),
                Asset.metadata_["title"].astext.ilike(f"%{token}%"),
                Asset.metadata_["description"].astext.ilike(f"%{token}%"),
                Asset.metadata_["original_path"].astext.ilike(f"%{token}%"),
            )
            for token in tokens
        ]
        asset_query = session.query(Asset, ProjectAsset.project_id).outerjoin(
            ProjectAsset,
            ProjectAsset.asset_id == Asset.asset_id,
        )
        if pid is not None:
            asset_query = asset_query.filter(ProjectAsset.project_id == pid)
        asset_rows = (
            asset_query
            .filter(and_(*asset_filters))
            .order_by(Asset.created_at.desc())
            .limit(limit)
            .all()
        )

        projects = [
            {
                "project_id": str(row.project_id),
                "label": row.label,
                "source_path": row.source_path,
                "file_count": row.file_count,
                "kind": "project",
            }
            for row in project_rows
        ]

        assets = []
        for asset, asset_project_id in asset_rows:
            metadata = asset.metadata_ or {}
            if not _matches_search_tokens(
                asset.filename,
                asset.mime_type,
                metadata.get("title"),
                metadata.get("description"),
                metadata.get("original_path"),
                tokens=tokens,
            ):
                continue

            assets.append(
                {
                    "asset_id": str(asset.asset_id),
                    "project_id": str(asset_project_id) if asset_project_id else None,
                    "filename": asset.filename,
                    "mime_type": asset.mime_type,
                    "size_bytes": asset.size_bytes,
                    "status": asset.status.value,
                    "kind": "asset",
                }
            )

        return {
            "query": query,
            "tokens": tokens,
            "project_id": str(pid) if pid is not None else None,
            "projects": projects,
            "assets": assets[:limit],
            "total": len(projects) + len(assets[:limit]),
        }


# ── Spaces ───────────────────────────────────────────────────────
