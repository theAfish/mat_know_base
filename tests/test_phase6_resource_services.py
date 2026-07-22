import hashlib

import pytest

from mkb import ConflictError, KnowledgeBase, OperationReceipt


def _client(tmp_path):
    return KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'phase6.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
    )


def test_collection_update_and_safe_delete(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Original", metadata={"version": 1})

        updated = kb.collections.update(
            collection.id,
            name="Updated",
            source_path="/external/reference",
            metadata={"version": 2},
        )

        assert updated.name == "Updated"
        assert updated.source_path == "/external/reference"
        assert updated.metadata == {"version": 2}
        receipt = kb.collections.delete(collection.id)
        assert isinstance(receipt, OperationReceipt)
        assert receipt.resource_id == collection.id
        assert kb.collections.get(collection.id) is None

        nonempty = kb.collections.create(name="Nonempty")
        kb.sources.add_text(nonempty.id, "content")
        with pytest.raises(ConflictError, match="not empty"):
            kb.collections.delete(nonempty.id)


def test_collection_group_lifecycle_and_assignment(tmp_path):
    with _client(tmp_path) as kb:
        assert kb.initialize() == 8
        first = kb.collections.create(name="First")
        second = kb.collections.create(name="Second")
        group = kb.collections.groups.create(
            name="Battery materials",
            color="#334455",
            display_order=2,
        )

        receipt = kb.collections.assign_group([first.id, second.id], group.id)
        assert receipt.details == {"updated": 2}
        assert kb.collections.require(first.id).group_id == group.id
        assert kb.collections.groups.require(group.id).collection_count == 2

        revised = kb.collections.groups.update(group.id, name="Electrodes")
        assert revised.name == "Electrodes"
        deleted = kb.collections.groups.delete(group.id)
        assert deleted.details == {"unassigned_collections": 2}
        assert kb.collections.require(second.id).group_id is None


def test_file_directory_uri_and_structured_record_ingestion(tmp_path):
    directory = tmp_path / "inputs"
    directory.mkdir()
    (directory / "a.txt").write_text("alpha", encoding="utf-8")
    nested = directory / "nested"
    nested.mkdir()
    (nested / "b.json").write_text('{"value": 2}', encoding="utf-8")

    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Ingestion forms")

        managed = kb.sources.add_file(collection.id, directory / "a.txt")
        directory_sources = kb.sources.add_directory(collection.id, nested)
        external = kb.sources.add_uri(
            collection.id,
            "https://example.test/dataset.csv",
            media_type="text/csv",
            metadata={"owner": "external"},
        )
        records = kb.sources.add_records(
            collection.id,
            [{"sample": "A", "value": 1}, {"sample": "B", "value": None}],
        )

        assert kb.sources.read_bytes(managed.id) == b"alpha"
        assert len(directory_sources) == 1
        assert directory_sources[0].metadata["relative_path"] == "b.json"
        reloaded_external = kb.sources.require(external.id)
        assert reloaded_external.uri == "https://example.test/dataset.csv"
        assert reloaded_external.storage is None
        assert reloaded_external.metadata == {"owner": "external"}
        with pytest.raises(ConflictError, match="external reference"):
            kb.sources.open(external.id)
        assert records.media_type == "application/json"
        assert records.metadata["record_count"] == 2
        assert b'"sample": "A"' in kb.sources.read_bytes(records.id)


def test_external_artifact_registration_preserves_existing_object(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="External artifact")
        source = kb.sources.add_text(collection.id, "raw")
        content = b"externally processed output"
        key = "external/processed.txt"
        kb.object_store.put_bytes("processed", key, content)

        artifact = kb.artifacts.register(
            source.id,
            bucket="processed",
            key=key,
            processing_type="EXTERNAL",
            format="txt",
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            primary_path="processed.txt",
            metadata={"processor": "external-tool/2"},
        )

        assert kb.artifacts.read_bytes(artifact.id) == content
        assert artifact.source_sha256 == source.sha256
        assert artifact.metadata == {"processor": "external-tool/2"}


def test_record_query_evidence_and_versioned_schema_lifecycle(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Queryable")
        source = kb.sources.add_text(collection.id, "evidence")
        matching = kb.records.create(
            collection_id=collection.id,
            data={"material": "steel", "temperature": 900},
        )
        kb.records.create(
            collection_id=collection.id,
            data={"material": "aluminum", "temperature": 500},
        )
        link = kb.evidence.create(
            output_type="record",
            output_id=matching.id,
            source_id=source.id,
            locator={"page": 1},
        )

        assert kb.records.query(filters={"material": "steel"}) == [matching]
        assert kb.records.evidence(matching.id) == [link]

        schema = kb.schemas.create(
            name="lifecycle",
            domain="materials",
            definition={"type": "object"},
            system_prompt="Extract.",
        )
        revised = kb.schemas.update(
            schema.id,
            definition={"type": "object", "required": ["material"]},
        )
        assert revised.id == schema.id
        assert revised.version == 2
        receipt = kb.schemas.delete(schema.id)
        assert receipt.resource_id == schema.id
        assert kb.schemas.get(schema.id) is None


def test_schema_delete_refuses_referenced_schema(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Protected")
        record = kb.records.create(collection_id=collection.id, data={})
        schema = kb.schemas.create(
            name="protected",
            domain="materials",
            definition={},
            system_prompt="Extract.",
        )
        kb.projections.create(schema_id=schema.id, record_id=record.id, data={})

        with pytest.raises(ConflictError, match="in use"):
            kb.schemas.delete(schema.id)
