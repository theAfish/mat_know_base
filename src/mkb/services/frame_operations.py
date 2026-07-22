"""Explicit resource adapter for frame extraction and inspection."""

from __future__ import annotations

from mkb.ports import Database, ObjectStore


class FrameOperations:
    def __init__(self, database: Database, object_store: ObjectStore | None):
        self._database = database
        self._object_store = object_store

    def extract(self, **kwargs):
        from mkb.services.frames import extract

        return extract(
            database=self._database, object_store=self._object_store, **kwargs
        )

    def get_frame(self, project_id, **kwargs):
        from mkb.services.frames import get_frame

        return get_frame(project_id, database=self._database, **kwargs)

    def list_frames(self, **kwargs):
        from mkb.services.frames import list_frames

        return list_frames(database=self._database, **kwargs)

    def get_extraction_history(self, project_id, **kwargs):
        from mkb.services.frames import get_extraction_history

        return get_extraction_history(project_id, database=self._database, **kwargs)
