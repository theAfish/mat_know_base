"""Base-package integration with a caller-owned store; no SQL or services.

This intentionally small read/write adapter demonstrates wrapping host storage.
Implement the remaining Writable*Repository methods when your host uses them.
"""

from uuid import UUID, uuid4

from mkb import Collection, KnowledgeBase, RepositoryBundle


class HostCollections:
    def __init__(self):
        self.rows: dict[UUID, Collection] = {}

    def create(self, *, name, source_path=None, metadata=None, collection_id=None):
        row = Collection(
            id=collection_id or uuid4(), name=name,
            source_path=source_path, metadata=dict(metadata or {}),
        )
        self.rows[row.id] = row
        return row

    def get(self, collection_id):
        return self.rows.get(UUID(str(collection_id)))

    def list(self, *, limit=100, offset=0):
        return list(self.rows.values())[offset:offset + limit]


def main():
    host_store = HostCollections()
    with KnowledgeBase.from_repositories(RepositoryBundle(collections=host_store)) as kb:
        row = kb.collections.create(name="Host-owned knowledge")
        assert kb.collections.require(row.id).name == "Host-owned knowledge"
    # The host keeps ownership and can continue using the store.
    assert host_store.get(row.id) == row
    print("custom storage example passed without SQL or external services")


if __name__ == "__main__":
    main()
