import json
import uuid
from datetime import datetime, timezone

import pytest

from mkb.exceptions import NotFoundError, ValidationError
from mkb.models import (
    Artifact,
    Collection,
    ExtractionSchema,
    Projection,
    Record,
    Source,
    StorageReference,
)
from mkb.repositories import (
    Artifacts,
    Collections,
    ExtractionSchemas,
    Projections,
    Records,
    Sources,
)


class _Repository:
    def __init__(self):
        self.row = Collection(
            id=uuid.uuid4(),
            name="Example",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.calls = []

    def get(self, collection_id):
        self.calls.append(("get", collection_id))
        return self.row if str(collection_id) == str(self.row.id) else None

    def list(self, *, limit, offset):
        self.calls.append(("list", limit, offset))
        return [self.row]


def test_collections_are_typed_and_json_serializable():
    repository = _Repository()
    collections = Collections(repository)

    result = collections.list(limit=20, offset=2)

    assert result == [repository.row]
    assert result[0].model_dump(mode="json")["id"] == str(repository.row.id)
    assert repository.calls == [("list", 20, 2)]


def test_collections_validate_pagination_and_get_optional_row():
    repository = _Repository()
    collections = Collections(repository)

    assert collections.get(repository.row.id) == repository.row
    assert collections.get(uuid.uuid4()) is None
    with pytest.raises(ValidationError, match="between"):
        collections.list(limit=0)
    with pytest.raises(ValidationError, match="non-negative"):
        collections.list(offset=-1)
    with pytest.raises(ValidationError, match="UUID"):
        collections.get("not-a-uuid")


class _ContentRepository:
    def __init__(self, row):
        self.row = row

    def get(self, identifier):
        return self.row if str(identifier) == str(self.row.id) else None

    def list(self, **_kwargs):
        return [self.row]


class _ObjectStore:
    def __init__(self):
        self.values = {("raw", "key"): b"content"}

    def get_bytes(self, bucket, key):
        return self.values[(bucket, key)]

    def open(self, bucket, key):
        import io

        return io.BytesIO(self.get_bytes(bucket, key))

    def exists(self, bucket, key):
        return (bucket, key) in self.values


def test_sources_read_content_by_typed_storage_reference():
    row = Source(
        id=uuid.uuid4(),
        filename="paper.pdf",
        media_type="application/pdf",
        size=7,
        sha256="abc",
        status="STORED",
        storage=StorageReference(bucket="raw", key="key"),
    )
    sources = Sources(_ContentRepository(row), _ObjectStore())

    assert sources.list() == [row]
    assert sources.read_bytes(row.id) == b"content"
    assert sources.open(row.id).read() == b"content"
    assert sources.content_exists(row.id) is True
    with pytest.raises(NotFoundError, match="Source"):
        sources.read_bytes(uuid.uuid4())
    with pytest.raises(ValidationError, match="UUID"):
        sources.get("bad")


def test_artifacts_read_content_and_filter_by_source():
    source_id = uuid.uuid4()
    row = Artifact(
        id=uuid.uuid4(),
        source_id=source_id,
        processing_type="MARKDOWN",
        format="md",
        size=7,
        sha256="def",
        source_sha256="abc",
        storage=StorageReference(bucket="raw", key="key"),
    )
    artifacts = Artifacts(_ContentRepository(row), _ObjectStore())

    assert artifacts.list(source_id=source_id) == [row]
    assert artifacts.read_bytes(row.id) == b"content"
    assert artifacts.content_exists(row.id) is True
    with pytest.raises(NotFoundError, match="Artifact"):
        artifacts.open(uuid.uuid4())
    with pytest.raises(ValidationError, match="UUID"):
        artifacts.list(source_id="bad")


class _RecordRepository(_ContentRepository):
    def get_for_collection(self, collection_id):
        return self.row if str(collection_id) == str(self.row.collection_id) else None


def test_records_preserve_nested_json_and_support_collection_queries():
    payload = {
        "measurements": [{"value": 0, "unit": None}],
        "flags": [False, True],
        "empty": {},
    }
    row = Record(
        id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        status="COMPLETED",
        data=payload,
        source_metadata={"pages": [1, 3]},
        annotations={"clarifications": []},
    )
    records = Records(_RecordRepository(row))

    result = records.get_for_collection(row.collection_id)

    assert result == row
    assert result.model_dump(mode="json")["data"] == payload
    assert records.list(collection_id=row.collection_id, status="COMPLETED") == [row]
    assert json.loads(records.export_json(indent=None))[0]["data"] == payload
    with pytest.raises(NotFoundError, match="Record"):
        records.require(uuid.uuid4())


class _SchemaRepository:
    def __init__(self, row):
        self.row = row

    def get(self, identifier):
        return self.row if str(identifier) == str(self.row.id) else None

    def get_by_name(self, name):
        return self.row if name == self.row.name else None

    def get_version(self, identifier, version):
        if str(identifier) == str(self.row.id) and version == self.row.version:
            return self.row
        return None

    def list_versions(self, identifier, **_kwargs):
        return [self.row] if str(identifier) == str(self.row.id) else []

    def list(self, **_kwargs):
        return [self.row]


def test_extraction_schemas_resolve_by_id_or_name_and_serialize():
    row = ExtractionSchema(
        id=uuid.uuid4(),
        name="custom-results",
        domain="consumer domain",
        purpose="freeform",
        definition={"type": "array", "items": {"type": "object"}},
        system_prompt="Extract supported values.",
        post_processors=({"name": "clean"},),
    )
    schemas = ExtractionSchemas(_SchemaRepository(row))

    assert schemas.get(row.id) == row
    assert schemas.get(row.name) == row
    assert schemas.get_version(row.id, 1) == row
    assert schemas.history(row.id) == [row]
    assert schemas.list()[0].model_dump(mode="json")["definition"] == row.definition
    with pytest.raises(ValidationError, match="must not be empty"):
        schemas.get(" ")


class _ProjectionRepository(_ContentRepository):
    pass


def test_projections_preserve_arbitrary_json_and_validate_filters():
    payload = [{"question": "q", "answer": ["a", None, 0]}]
    row = Projection(
        id=uuid.uuid4(),
        schema_id=uuid.uuid4(),
        record_id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        source_type="frame",
        status="COMPLETED",
        data=payload,
        schema_version=3,
        supersedes_ids=(str(uuid.uuid4()),),
    )
    projections = Projections(_ProjectionRepository(row))

    result = projections.list(
        schema_id=row.schema_id,
        record_id=row.record_id,
        collection_id=row.collection_id,
        status="COMPLETED",
        include_history=True,
        newest_only=True,
    )

    assert result == [row]
    assert result[0].model_dump(mode="json")["data"] == payload
    assert json.loads(projections.export_json(indent=None))[0]["data"] == payload
    with pytest.raises(ValidationError, match="UUID"):
        projections.list(record_id="bad")
    with pytest.raises(NotFoundError, match="Projection"):
        projections.require(uuid.uuid4())
