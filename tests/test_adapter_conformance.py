import io
import uuid
from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text

from mkb import KnowledgeBase, Pipeline, Step
from mkb.adapters import (
    FileObjectStore,
    GenericArtifactRepository,
    GenericCollectionGroupRepository,
    GenericCollectionRepository,
    GenericEvidenceRepository,
    GenericFeedbackRepository,
    GenericExtractionSchemaRepository,
    GenericProjectionRepository,
    GenericPostProcessorRepository,
    GenericRecordRepository,
    GenericSourceRepository,
    GenericSkillRepository,
    InMemoryGraphStore,
    S3ObjectStore,
    SQLAlchemyArtifactRepository,
    SQLAlchemyCollectionGroupRepository,
    SQLAlchemyCollectionRepository,
    SQLAlchemyDatabase,
    SQLAlchemyExtractionSchemaRepository,
    SQLAlchemyFeedbackRepository,
    SQLAlchemyPostProcessorRepository,
    SQLAlchemyProjectionRepository,
    SQLAlchemyRecordRepository,
    SQLAlchemySourceRepository,
    SQLAlchemySkillRepository,
    SQLAlchemyWorkflowRepository,
)
from mkb.models import Entity, Relation
from mkb.managed_services import (
    FeedbackRepository,
    PostProcessorRepository,
    SkillRepository,
)
from mkb.ports import (
    Capabilities,
    Database,
    GraphStore,
    ObjectStore,
    VectorSearch,
)
from mkb.repositories import (
    ArtifactRepository,
    CollectionGroupRepository,
    CollectionRepository,
    EvidenceRepository,
    ExtractionSchemaRepository,
    ProjectionRepository,
    RecordRepository,
    SourceRepository,
    WorkflowRepository,
)


class _Paginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, *, Bucket, Prefix):
        contents = [
            {
                "Key": key,
                "Size": len(value),
                "ETag": f'"etag-{key}"',
                "LastModified": datetime.now(timezone.utc),
            }
            for (bucket, key), value in sorted(self.client.values.items())
            if bucket == Bucket and key.startswith(Prefix)
        ]
        return [{"Contents": contents}]


class _S3Client:
    def __init__(self):
        self.values = {}
        self.closed = False

    def put_object(self, *, Bucket, Key, Body):
        self.values[(Bucket, Key)] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.values[(Bucket, Key)])}

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.values:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def delete_object(self, *, Bucket, Key):
        self.values.pop((Bucket, Key), None)

    def get_paginator(self, _name):
        return _Paginator(self)

    def head_bucket(self, *, Bucket):
        return {"Bucket": Bucket}

    def close(self):
        self.closed = True


@pytest.mark.parametrize("kind", ["filesystem", "s3"])
def test_object_store_conformance(kind, tmp_path):
    store = (
        FileObjectStore(tmp_path / "objects")
        if kind == "filesystem"
        else S3ObjectStore(client=_S3Client())
    )
    assert isinstance(store, ObjectStore)
    assert Capabilities.OBJECT_STREAMING in store.capabilities

    store.put_bytes("raw", "papers/example.txt", b"content")
    assert store.exists("raw", "papers/example.txt") is True
    assert store.get_bytes("raw", "papers/example.txt") == b"content"
    with store.open("raw", "papers/example.txt") as stream:
        assert stream.read() == b"content"
    items = list(store.list("raw", "papers/"))
    assert [(item.key, item.size) for item in items] == [("papers/example.txt", 7)]
    store.check(("raw",))
    store.delete("raw", "papers/example.txt")
    assert store.exists("raw", "papers/example.txt") is False
    store.close()
    with pytest.raises(RuntimeError, match="closed"):
        store.exists("raw", "papers/example.txt")


def test_database_and_graph_adapter_conformance(tmp_path):
    database = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'database.db'}")
    graph = InMemoryGraphStore()
    assert isinstance(database, Database)
    assert isinstance(graph, GraphStore)
    assert database.capabilities == frozenset({Capabilities.TRANSACTIONS})
    assert Capabilities.GRAPH_TRAVERSAL in graph.capabilities

    with database.transaction() as session:
        session.execute(text("create table example (value integer not null)"))
        session.execute(text("insert into example values (1)"))
    with database.session() as session:
        assert session.execute(text("select value from example")).scalar_one() == 1

    left = Entity(id=uuid.uuid4(), type="node", name="left")
    right = Entity(id=uuid.uuid4(), type="node", name="right")
    relation = Relation(
        id=uuid.uuid4(),
        source_id=left.id,
        target_id=right.id,
        type="linked",
    )
    graph.upsert_entity(left)
    graph.upsert_entity(right)
    graph.upsert_relation(relation)
    assert graph.get_entity(left.id) == left
    assert graph.get_relation(relation.id) == relation
    assert graph.list_relations(entity_id=left.id) == [relation]

    database.close()
    graph.close()


def test_repository_adapters_conform_structurally(tmp_path):
    database = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'repositories.db'}")
    try:
        generic = (
            (GenericCollectionRepository(database), CollectionRepository),
            (GenericCollectionGroupRepository(database), CollectionGroupRepository),
            (GenericSourceRepository(database), SourceRepository),
            (GenericArtifactRepository(database), ArtifactRepository),
            (GenericRecordRepository(database), RecordRepository),
            (GenericExtractionSchemaRepository(database), ExtractionSchemaRepository),
            (GenericProjectionRepository(database), ProjectionRepository),
            (GenericEvidenceRepository(database), EvidenceRepository),
            (GenericFeedbackRepository(database), FeedbackRepository),
            (GenericSkillRepository(database), SkillRepository),
            (GenericPostProcessorRepository(database), PostProcessorRepository),
        )
        legacy = (
            (SQLAlchemyCollectionRepository(database), CollectionRepository),
            (SQLAlchemyCollectionGroupRepository(database), CollectionGroupRepository),
            (SQLAlchemySourceRepository(database), SourceRepository),
            (SQLAlchemyArtifactRepository(database), ArtifactRepository),
            (SQLAlchemyRecordRepository(database), RecordRepository),
            (SQLAlchemyExtractionSchemaRepository(database), ExtractionSchemaRepository),
            (SQLAlchemyProjectionRepository(database), ProjectionRepository),
            (SQLAlchemyWorkflowRepository(database), WorkflowRepository),
            (SQLAlchemyFeedbackRepository(database), FeedbackRepository),
            (SQLAlchemySkillRepository(database), SkillRepository),
            (SQLAlchemyPostProcessorRepository(database), PostProcessorRepository),
        )
        for adapter, protocol in generic + legacy:
            assert isinstance(adapter, protocol)
    finally:
        database.close()


def test_vector_capability_is_composed_and_checked_before_pipeline_execution(tmp_path):
    class Vectors:
        capabilities = frozenset({Capabilities.VECTOR_SEARCH})

        def upsert(self, _namespace, vectors):
            return len(list(vectors))

        def query(self, _namespace, _vector, *, limit=10, filters=None):
            return []

        def delete(self, _namespace, identifiers):
            return len(list(identifiers))

        def close(self):
            self.closed = True

    vectors = Vectors()
    assert isinstance(vectors, VectorSearch)
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'vector.db'}",
        vector_search=vectors,
    )
    called = False

    def search(_context, _state):
        nonlocal called
        called = True
        return {}

    pipeline = Pipeline(
        name="vector",
        steps=(
            Step(
                name="search",
                handler=search,
                required_capabilities=frozenset({Capabilities.VECTOR_SEARCH}),
            ),
        ),
    )
    try:
        assert Capabilities.VECTOR_SEARCH in kb.pipelines.capabilities
        kb.pipelines.run(pipeline)
        assert called is True
    finally:
        kb.close()
    assert vectors.closed is True


def test_parser_adapter_registration_conformance():
    class PlainParser:
        name = "plain"
        source_types = frozenset({"text/plain"})

        def parse(self, content, *, parameters=None):
            return f"{(parameters or {}).get('prefix', '')}{content.decode()}"

    kb = KnowledgeBase()
    parser = kb.parsers.register_adapter(PlainParser())

    assert parser.name == "plain"
    assert (
        kb.parsers.parse(
            "plain",
            b"value",
            source_type="text/plain",
            parameters={"prefix": "parsed:"},
        )
        == "parsed:value"
    )
