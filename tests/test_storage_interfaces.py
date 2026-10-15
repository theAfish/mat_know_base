"""Host-supplied repositories work without a relational session or engine."""

from contextlib import contextmanager
from dataclasses import replace
import importlib.util
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from mkb.adapters import FileObjectStore
from mkb.exceptions import ConflictError
from mkb.models import Collection
from mkb.ports import Capabilities
from mkb.repositories import WritableCollectionRepository
from mkb.sdk import KnowledgeBase
from mkb.storage import RepositoryBundle


class MemoryCollections:
    def __init__(self):
        self.rows = {}

    def create(self, *, name, source_path=None, metadata=None, collection_id=None):
        row = Collection(
            id=collection_id or uuid.uuid4(),
            name=name,
            source_path=source_path,
            metadata=metadata or {},
        )
        self.rows[row.id] = row
        return row

    def get(self, collection_id):
        return self.rows.get(collection_id)

    def list(self, *, limit=100, offset=0):
        return list(self.rows.values())[offset : offset + limit]

    def update(self, collection_id, **changes):
        row = self.rows[collection_id].model_copy(
            update={key: value for key, value in changes.items() if value is not None}
        )
        self.rows[collection_id] = row
        return row

    def delete(self, collection_id):
        del self.rows[collection_id]


class MemorySources:
    def __init__(self):
        self.rows = {}

    def create(self, source, *, collection_id):
        row = source.model_copy(update={"collection_ids": (collection_id,)})
        self.rows[row.id] = row
        return row

    def get(self, source_id):
        return self.rows.get(source_id)

    def list(self, *, collection_id=None, limit=100, offset=0):
        rows = [
            row
            for row in self.rows.values()
            if collection_id is None or collection_id in row.collection_ids
        ]
        return rows[offset : offset + limit]


def test_custom_repositories_support_crud_and_explicit_schema_lifecycle():
    class Schema:
        current = None

        def initialize(self):
            self.current = 3
            return self.current

        def version(self):
            return self.current

    repository = MemoryCollections()
    assert isinstance(repository, WritableCollectionRepository)
    with KnowledgeBase.from_repositories(
        RepositoryBundle(collections=repository, schema_manager=Schema())
    ) as kb:
        assert kb.database is None
        assert kb.supports_transactions is False
        assert kb.schema_version() is None
        assert kb.initialize() == 3
        assert kb.schema_version() == 3
        collection = kb.collections.create(name=" Research ")
        assert collection.name == "Research"
        assert kb.collections.require(str(collection.id)) == collection
        assert kb.collections.update(collection.id, name="Updated").name == "Updated"
        kb.collections.delete(collection.id)
        assert kb.collections.list() == []


def test_metadata_repository_does_not_require_object_storage():
    sources = MemorySources()
    with KnowledgeBase.from_repositories(RepositoryBundle(sources=sources)) as kb:
        source = kb.sources.add_uri(uuid.uuid4(), "https://example.org/paper.pdf")
        assert kb.sources.require(source.id).uri == "https://example.org/paper.pdf"
        with pytest.raises(ConflictError, match="object store"):
            kb.sources.add_text(uuid.uuid4(), "content")
        assert len(sources.rows) == 1


@pytest.mark.parametrize("own", [False, True])
def test_custom_resource_ownership_is_explicit_and_close_is_idempotent(own):
    closed = []

    class Provider:
        capabilities = frozenset()

        def close(self):
            closed.append("model")

    kb = KnowledgeBase.from_repositories(
        RepositoryBundle(close=lambda: closed.append("repositories")),
        model_provider=Provider(),
        close_resources=own,
    )
    kb.close()
    kb.close()
    assert closed == (["model", "repositories"] if own else [])
    assert kb.closed
    with pytest.raises(RuntimeError, match="closed"):
        kb.initialize()


def test_close_attempts_all_owned_resources_when_one_fails():
    closed = []

    class Provider:
        capabilities = frozenset()

        def close(self):
            raise RuntimeError("model close failed")

    kb = KnowledgeBase.from_repositories(
        RepositoryBundle(close=lambda: closed.append("repositories")),
        model_provider=Provider(),
        close_resources=True,
    )
    with pytest.raises(RuntimeError, match="model close failed"):
        kb.close()
    assert kb.closed
    assert closed == ["repositories"]
    kb.close()


def test_failed_worker_shutdown_preserves_resources_until_close_can_be_retried(monkeypatch):
    closed = []
    kb = KnowledgeBase.from_repositories(
        RepositoryBundle(close=lambda: closed.append("repositories")),
        close_resources=True,
    )
    close_pipelines = kb.pipelines._close

    def worker_still_running():
        raise ConflictError("Pipeline workers did not stop before close")

    monkeypatch.setattr(kb.pipelines, "_close", worker_still_running)
    with pytest.raises(ConflictError, match="workers did not stop"):
        kb.close()
    assert kb.closed is False
    assert closed == []
    monkeypatch.setattr(kb.pipelines, "_close", close_pipelines)
    kb.close()
    assert kb.closed is True
    assert closed == ["repositories"]


def test_custom_unit_of_work_rolls_back_objects_and_repository_writes(tmp_path):
    collections = MemoryCollections()
    sources = MemorySources()
    store = FileObjectStore(tmp_path)
    events = []
    # This test writes collections and sources only. The other repositories are
    # read-only placeholders for the unused grouped services in a unit of work.
    scoped = RepositoryBundle(
        collections=collections,
        sources=sources,
        records=MemoryCollections(),
        schemas=MemoryCollections(),
        projections=MemoryCollections(),
    )

    @contextmanager
    def transaction():
        saved_collections = dict(collections.rows)
        saved_sources = dict(sources.rows)
        try:
            yield scoped
        except Exception:
            collections.rows = saved_collections
            sources.rows = saved_sources
            events.append("rollback")
            raise
        else:
            events.append("commit")

    with KnowledgeBase.from_repositories(
        replace(scoped, transaction=transaction),
        object_store=store,
    ) as kb:
        assert kb.database is None
        assert kb.supports_transactions
        assert Capabilities.TRANSACTIONS in kb.pipelines.capabilities
        with kb.transaction() as tx:
            retained = tx.collections.create(name="keep")
        with pytest.raises(RuntimeError, match="abort"):
            with kb.transaction() as tx:
                collection = tx.collections.create(name="rollback")
                tx.sources.add_text(collection.id, "temporary content")
                raise RuntimeError("abort")
        assert kb.collections.list() == [retained]
        assert kb.sources.list() == []
        assert list(store.list("raw")) == []
        assert events == ["commit", "rollback"]


def test_transaction_requires_scoped_versions_of_configured_repositories():
    scoped = RepositoryBundle(
        collections=MemoryCollections(),
        records=MemoryCollections(),
        schemas=MemoryCollections(),
        projections=MemoryCollections(),
    )

    @contextmanager
    def transaction():
        yield scoped

    with KnowledgeBase.from_repositories(
        replace(scoped, sources=MemorySources(), transaction=transaction)
    ) as kb:
        with pytest.raises(ConflictError, match="missing repositories: sources"):
            with kb.transaction():
                pytest.fail("An incomplete transaction must not run application code")


@pytest.mark.parametrize("sql", [False, True])
def test_constructor_never_imports_legacy_application_or_unselected_backends(sql):
    if sql and importlib.util.find_spec("sqlalchemy") is None:
        pytest.skip("SQL adapter extra is not installed")
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = f"""
import importlib.abc
import sys
sys.path.insert(0, {str(source_root)!r})

class RejectOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ('mkb.config', 'mkb.db', 'mkb.services', 'mkb.agents', 'mkb.api')
        if not {sql!r}:
            blocked += ('sqlalchemy',)
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('unexpected import: ' + fullname)

sys.meta_path.insert(0, RejectOptionalImports())
from mkb import KnowledgeBase
from mkb.storage import RepositoryBundle
from mkb.adapters import FileObjectStore
if {sql!r}:
    with KnowledgeBase.from_url(database_url='sqlite:///:memory:') as kb:
        kb.initialize()
        assert kb.collections.create(name='isolated').name == 'isolated'
else:
    with KnowledgeBase.from_repositories(RepositoryBundle()) as kb:
        assert kb.database is None
        assert not kb.supports_transactions
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
