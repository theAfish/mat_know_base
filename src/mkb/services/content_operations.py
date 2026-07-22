"""Explicit resource adapter for ingestion and processing operations."""

from __future__ import annotations

from mkb.ports import Database, ObjectStore


class ContentOperations:
    """Run content lifecycle operations against one injected resource set."""

    def __init__(
        self,
        database: Database,
        object_store: ObjectStore,
        *,
        raw_bucket: str,
        processed_bucket: str,
    ):
        self._database = database
        self._object_store = object_store
        self._raw_bucket = raw_bucket
        self._processed_bucket = processed_bucket

    def ingest(self, directory, label=None, *, user_named: bool = False) -> dict:
        from mkb.ingest.worker import ingest_directory

        return ingest_directory(
            directory,
            label=label,
            user_named=user_named,
            database=self._database,
            object_store=self._object_store,
            raw_bucket=self._raw_bucket,
        )

    def sync(self, root_dir) -> dict:
        from mkb.ingest.worker import sync_root

        return sync_root(
            root_dir,
            database=self._database,
            object_store=self._object_store,
            raw_bucket=self._raw_bucket,
        )

    def sync_project(self, project_id) -> dict:
        import uuid

        from mkb.ingest.worker import sync_project

        return sync_project(
            uuid.UUID(str(project_id)),
            database=self._database,
            object_store=self._object_store,
            raw_bucket=self._raw_bucket,
        )

    def process(self, project_id=None, progress_callback=None) -> dict:
        from mkb.db.models import ProjectAsset
        from mkb.processors.coordinator import process_all_pending, process_asset

        if project_id is None:
            return process_all_pending(
                database=self._database,
                object_store=self._object_store,
                processed_bucket=self._processed_bucket,
                progress_callback=progress_callback,
            )

        import uuid

        project_id = uuid.UUID(str(project_id))
        with self._database.session() as session:
            asset_ids = [
                link.asset_id
                for link in session.query(ProjectAsset)
                .filter_by(project_id=project_id)
                .all()
            ]
        results = []
        for asset_id in asset_ids:
            if progress_callback:
                progress_callback(
                    {
                        "message": f"Starting asset {len(results) + 1}/{len(asset_ids)}",
                        "asset_id": str(asset_id),
                    }
                )
            try:
                results.append(
                    process_asset(
                        asset_id,
                        database=self._database,
                        object_store=self._object_store,
                        processed_bucket=self._processed_bucket,
                        progress_callback=progress_callback,
                    )
                )
            except Exception as exc:
                results.append({"asset_id": str(asset_id), "error": str(exc)})
        return {
            "project_id": str(project_id),
            "assets_processed": len(results),
            "results": results,
        }
