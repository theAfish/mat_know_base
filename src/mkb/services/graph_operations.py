"""Explicit resource adapter for materials knowledge-graph operations."""

from __future__ import annotations

from mkb.ports import Database, ObjectStore


class GraphOperations:
    def __init__(self, database: Database, object_store: ObjectStore | None):
        self._database = database
        self._object_store = object_store

    def clear_knowledge_graphs(self, **kwargs):
        from mkb.services.graphs import clear_knowledge_graphs

        return clear_knowledge_graphs(database=self._database, **kwargs)

    def extract_knowledge_graph(self, **kwargs):
        from mkb.services.graphs import extract_knowledge_graph

        return extract_knowledge_graph(
            database=self._database,
            object_store=self._object_store,
            **kwargs,
        )

    def get_knowledge_graph(self, **kwargs):
        from mkb.services.graphs import get_knowledge_graph

        return get_knowledge_graph(database=self._database, **kwargs)

    def review_knowledge_graph(self, **kwargs):
        from mkb.services.graphs import review_knowledge_graph

        return review_knowledge_graph(
            database=self._database,
            object_store=self._object_store,
            **kwargs,
        )

    def get_graph_review_counts(self, **kwargs):
        from mkb.services.graphs import get_graph_review_counts

        return get_graph_review_counts(database=self._database, **kwargs)
