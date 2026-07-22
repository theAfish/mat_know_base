"""Explicit resource adapter for legacy asset service operations."""

from __future__ import annotations

from mkb.ports import Database, ObjectStore


class AssetOperations:
    def __init__(self, database: Database, object_store: ObjectStore, *, processed_bucket: str):
        self._database = database
        self._object_store = object_store
        self._processed_bucket = processed_bucket

    def __getattr__(self, name):
        from mkb.services import assets

        operation = getattr(assets, name)
        def bound(*args, **kwargs):
            resources = {"database": self._database}
            if name == "link_manual_processed_data":
                resources.update(
                    object_store=self._object_store,
                    processed_bucket=self._processed_bucket,
                )
            return operation(*args, **resources, **kwargs)
        return bound
