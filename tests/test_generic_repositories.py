import json
import uuid

import pytest
from sqlalchemy import delete, text

from mkb import ConflictError, KnowledgeBase, NotFoundError, ValidationError
from mkb.adapters.generic_repositories import (
    artifacts_table,
    evidence_table,
    jobs_table,
    projections_table,
    schema_migrations_table,
    schema_versions_table,
)


def _client(tmp_path, name="sdk.db"):
    return KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / name}")


def test_explicit_initialization_is_idempotent_and_enables_typed_writes(tmp_path):
    with _client(tmp_path) as kb:
        assert kb.schema_version() is None
        assert kb.initialize() == 8
        assert kb.initialize() == 8
        assert kb.schema_version() == 8
        with kb.database.session() as session:
            versions = list(
                session.scalars(
                    text("select version from mkb_schema_migrations order by version")
                )
            )
        assert versions == [1, 2, 3, 4, 5, 6, 7, 8]

        collection = kb.collections.create(
            name="Experiment 42",
            metadata={"owner": "consumer-project"},
        )
        record = kb.records.create(
            collection_id=collection.id,
            data=[{"temperature": 800, "unit": "C"}],
            source_metadata={"source": "manual"},
        )
        schema = kb.schemas.create(
            name="synthesis-result",
            domain="custom materials",
            definition={"type": "array", "items": {"type": "object"}},
            system_prompt="Extract the requested fields.",
        )

        assert kb.collections.require(collection.id).metadata["owner"] == "consumer-project"
        assert kb.records.require(record.id).data == record.data
        assert kb.records.get_for_collection(collection.id).id == record.id
        assert kb.schemas.require("synthesis-result").id == schema.id
        assert kb.records.export_json(indent=None).startswith("[")


def test_client_refuses_portable_schema_newer_than_supported(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        with kb.database.transaction() as session:
            session.execute(
                text(
                    "insert into mkb_schema_migrations "
                    "(version, name, applied_at) values (999, 'future', CURRENT_TIMESTAMP)"
                )
            )
        with pytest.raises(ConflictError, match="newer than this SDK supports"):
            kb.schema_version()


def test_transaction_commits_all_relational_writes_together(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()

        with kb.transaction() as tx:
            collection = tx.collections.create(name="Atomic collection")
            record = tx.records.create(
                collection_id=collection.id,
                data={"inside": "same transaction"},
            )
            schema = tx.schemas.create(
                name="atomic-schema",
                domain="example",
                definition={"type": "object"},
                system_prompt="Extract.",
            )
            projection = tx.projections.create(
                schema_id=schema.id,
                record_id=record.id,
                data={"result": 12.4},
                validation={"valid": True},
            )
            assert tx.records.require(record.id).data["inside"] == "same transaction"
            assert tx.projections.require(projection.id).data["result"] == 12.4

        assert kb.collections.require(collection.id).name == "Atomic collection"
        assert kb.records.require(record.id).collection_id == collection.id
        assert kb.schemas.require(schema.id).name == "atomic-schema"
        assert kb.projections.require(projection.id).collection_id == collection.id
        assert [
            item.id
            for item in kb.projections.list(schema_id=schema.id, record_id=record.id)
        ] == [projection.id]

        newer = kb.projections.create(
            schema_id=schema.id,
            record_id=record.id,
            data={"result": 13.1},
        )
        assert kb.projections.list(newest_only=True)[0].id == newer.id


def test_transaction_rolls_back_and_constraints_raise_typed_errors(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        rolled_back_id = uuid.uuid4()

        with pytest.raises(RuntimeError, match="cancel"):
            with kb.transaction() as tx:
                tx.collections.create(
                    name="Rolled back",
                    collection_id=rolled_back_id,
                )
                raise RuntimeError("cancel")

        assert kb.collections.get(rolled_back_id) is None

        kb.collections.create(name="Unique")
        with pytest.raises(ConflictError, match="already exists"):
            kb.collections.create(name="Unique")
        with pytest.raises(NotFoundError, match="Collection"):
            kb.records.create(collection_id=uuid.uuid4(), data={})
        with pytest.raises(ValidationError, match="JSON-compatible"):
            kb.records.create(
                collection_id=kb.collections.list()[0].id,
                data={"unsupported": object()},
            )


def test_transaction_commits_object_backed_writes_and_compensates_rollback(tmp_path):
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'object-transaction.db'}",
        object_store_url=(tmp_path / "transaction-objects").as_uri(),
    )
    with kb:
        kb.initialize()
        with kb.transaction() as tx:
            collection = tx.collections.create(name="Committed objects")
            source = tx.sources.add_text(collection.id, "source")
            artifact = tx.artifacts.add_bytes(
                source.id,
                b"artifact",
                processing_type="TEXT",
                format="txt",
            )

        assert kb.sources.require(source.id).id == source.id
        assert kb.artifacts.require(artifact.id).id == artifact.id
        assert kb.object_store.exists("raw", f"sources/{source.id}") is True
        assert kb.object_store.exists("processed", f"artifacts/{artifact.id}") is True

        rolled_back_collection_id = uuid.uuid4()
        rolled_back_source_id = uuid.uuid4()
        rolled_back_artifact_id = uuid.uuid4()
        with pytest.raises(RuntimeError, match="abort"):
            with kb.transaction() as tx:
                tx.collections.create(
                    name="Rolled back objects",
                    collection_id=rolled_back_collection_id,
                )
                rolled_back_source = tx.sources.add_text(
                    rolled_back_collection_id,
                    "temporary",
                    source_id=rolled_back_source_id,
                )
                tx.artifacts.add_bytes(
                    rolled_back_source.id,
                    b"temporary artifact",
                    processing_type="TEXT",
                    format="txt",
                    artifact_id=rolled_back_artifact_id,
                )
                raise RuntimeError("abort")

        assert kb.collections.get(rolled_back_collection_id) is None
        assert kb.sources.get(rolled_back_source_id) is None
        assert kb.artifacts.get(rolled_back_artifact_id) is None
        assert kb.object_store.exists("raw", f"sources/{rolled_back_source_id}") is False
        assert (
            kb.object_store.exists(
                "processed", f"artifacts/{rolled_back_artifact_id}"
            )
            is False
        )


def test_text_sources_use_object_store_and_compensate_failed_metadata_writes(tmp_path):
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'sources.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
    )
    with kb:
        kb.initialize()
        collection = kb.collections.create(name="Source collection")

        source = kb.sources.add_text(
            collection.id,
            "locally managed text",
            filename="notes.txt",
            metadata={"kind": "notes"},
        )

        assert kb.sources.read_bytes(source.id) == b"locally managed text"
        assert kb.sources.require(source.id).collection_ids == (collection.id,)
        assert kb.collections.require(collection.id).source_count == 1

        failed_source_id = uuid.uuid4()
        with pytest.raises(NotFoundError, match="Collection"):
            kb.sources.add_text(
                uuid.uuid4(),
                "must be compensated",
                source_id=failed_source_id,
            )
        assert kb.object_store.exists("raw", f"sources/{failed_source_id}") is False


def test_external_source_uri_does_not_consume_a_metadata_key(tmp_path):
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'external-source.db'}",
        object_store_url=(tmp_path / "external-source-objects").as_uri(),
    )
    with kb:
        kb.initialize()
        collection = kb.collections.create(name="External sources")
        metadata = {
            "_mkb_external_uri": "consumer-owned metadata",
            "nested": {"values": [1, None, False]},
        }

        source = kb.sources.add_uri(
            collection.id,
            "https://example.test/paper.pdf",
            metadata=metadata,
        )
        reloaded = kb.sources.require(source.id)

        assert reloaded.uri == "https://example.test/paper.pdf"
        assert reloaded.metadata == metadata


def test_external_uri_migration_backfills_without_removing_legacy_metadata(tmp_path):
    source_id = uuid.uuid4()
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'external-source-upgrade.db'}",
        object_store_url=(tmp_path / "external-source-upgrade-objects").as_uri(),
    )
    with kb:
        with kb.database.transaction() as session:
            session.execute(
                text(
                    """
                    CREATE TABLE mkb_sources (
                        id VARCHAR(36) PRIMARY KEY,
                        filename VARCHAR(1024) NOT NULL,
                        media_type VARCHAR(255) NOT NULL,
                        size INTEGER NOT NULL,
                        sha256 VARCHAR(64) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        bucket VARCHAR(255) NOT NULL,
                        object_key VARCHAR(2048) NOT NULL,
                        metadata JSON NOT NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    INSERT INTO mkb_sources (
                        id, filename, media_type, size, sha256, status,
                        bucket, object_key, metadata, created_at, updated_at
                    ) VALUES (
                        :id, 'legacy', 'application/octet-stream', 0, :sha256,
                        'REFERENCED', '', '', :metadata,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": str(source_id),
                    "sha256": "0" * 64,
                    "metadata": json.dumps(
                        {
                            "_mkb_external_uri": "https://legacy.test/source",
                            "owner": "consumer",
                        }
                    ),
                },
            )

        assert kb.initialize() == 8
        source = kb.sources.require(source_id)

        assert source.uri == "https://legacy.test/source"
        assert source.metadata == {
            "_mkb_external_uri": "https://legacy.test/source",
            "owner": "consumer",
        }


def test_artifacts_store_derived_bytes_and_preserve_source_provenance(tmp_path):
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'artifacts.db'}",
        object_store_url=(tmp_path / "artifact-objects").as_uri(),
    )
    with kb:
        kb.initialize()
        collection = kb.collections.create(name="Artifact collection")
        source = kb.sources.add_bytes(
            collection.id,
            b"raw source",
            filename="source.bin",
        )

        artifact = kb.artifacts.add_bytes(
            source.id,
            b"derived output",
            processing_type="NORMALIZED_TEXT",
            format="txt",
            primary_path="result.txt",
            metadata={"processor": "consumer"},
        )

        assert kb.artifacts.read_bytes(artifact.id) == b"derived output"
        assert kb.artifacts.require(artifact.id).source_sha256 == source.sha256
        assert [item.id for item in kb.artifacts.list(source_id=source.id)] == [
            artifact.id
        ]

        failed_artifact_id = uuid.uuid4()
        with pytest.raises(ValidationError, match="JSON-compatible"):
            kb.artifacts.add_bytes(
                source.id,
                b"invalid metadata output",
                processing_type="TEST",
                format="bin",
                metadata={"unsupported": object()},
                artifact_id=failed_artifact_id,
            )
        assert (
            kb.object_store.exists("processed", f"artifacts/{failed_artifact_id}")
            is False
        )


def test_projection_missing_dependencies_raise_without_partial_rows(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Projection collection")
        record = kb.records.create(collection_id=collection.id, data={})

        with pytest.raises(NotFoundError, match="Extraction schema"):
            kb.projections.create(
                schema_id=uuid.uuid4(),
                record_id=record.id,
                data={"unused": True},
            )

        assert kb.projections.list() == []


def test_projection_schema_version_resolves_immutable_historical_definition(tmp_path):
    with _client(tmp_path, "schema-history.db") as kb:
        kb.initialize()
        collection = kb.collections.create(name="Historical schema collection")
        record = kb.records.create(collection_id=collection.id, data={})
        schema = kb.schemas.create(
            name="versioned-results",
            domain="materials",
            definition={"properties": {"temperature": {"type": "number"}}},
            system_prompt="Extract temperature.",
        )
        projection = kb.projections.create(
            schema_id=schema.id,
            record_id=record.id,
            data={"temperature": 900},
        )

        second = kb.schemas.update(
            schema.id,
            definition={"properties": {"pressure": {"type": "number"}}},
            system_prompt="Extract pressure.",
        )
        third = kb.schemas.update(
            schema.id,
            definition={"properties": {"duration": {"type": "number"}}},
            system_prompt="Extract duration.",
        )

        historical = kb.schemas.require_version(
            projection.schema_id, projection.schema_version
        )
        assert historical.version == 1
        assert "temperature" in historical.definition["properties"]
        assert second.version == 2
        assert third.version == 3
        assert [item.version for item in kb.schemas.history(schema.id)] == [3, 2, 1]
        assert kb.schemas.require(schema.id).definition == third.definition

        with pytest.raises(RuntimeError, match="abort schema update"):
            with kb.transaction() as tx:
                tx.schemas.update(
                    schema.id,
                    definition={"properties": {"rolled_back": {"type": "boolean"}}},
                )
                raise RuntimeError("abort schema update")
        assert kb.schemas.require(schema.id).version == 3
        assert [item.version for item in kb.schemas.history(schema.id)] == [3, 2, 1]

        with pytest.raises(NotFoundError, match="version not found"):
            kb.schemas.require_version(schema.id, 99)
        with pytest.raises(ValidationError, match="positive integer"):
            kb.schemas.get_version(schema.id, 0)


def test_revision_seven_backfills_the_current_schema_definition(tmp_path):
    with _client(tmp_path, "schema-history-upgrade.db") as kb:
        kb.initialize()
        schema = kb.schemas.create(
            name="pre-history-schema",
            domain="materials",
            definition={"properties": {"current": {"type": "string"}}},
            system_prompt="Extract the current definition.",
        )

        # Simulate a portable database created before immutable schema history.
        with kb.database.transaction() as session:
            schema_versions_table.drop(session.connection())
            session.execute(
                delete(schema_migrations_table).where(
                    schema_migrations_table.c.version >= 7
                )
            )

        assert kb.schema_version() == 6
        kb.initialize()
        migrated = kb.schemas.require_version(schema.id, schema.version)
        assert migrated.definition == schema.definition
        assert migrated.system_prompt == schema.system_prompt


def test_evidence_links_persist_losslessly_and_share_transactions(tmp_path):
    with _client(tmp_path, "evidence.db") as kb:
        kb.initialize()
        collection = kb.collections.create(name="Evidence collection")
        record = kb.records.create(collection_id=collection.id, data={"claim": "supported"})
        source_id = uuid.uuid4()
        evidence_id = uuid.uuid4()

        with kb.transaction() as tx:
            evidence = tx.evidence.create(
                evidence_id=evidence_id,
                output_type="record",
                output_id=record.id,
                source_id=source_id,
                locator={"page": 7, "bbox": [1.25, 2.5, 4.0, 8.0]},
                excerpt="verbatim supporting statement",
                metadata={"review": {"status": "accepted"}},
            )

        reloaded = kb.evidence.require(evidence.id)
        assert reloaded.model_dump(mode="json") == evidence.model_dump(mode="json")
        assert [item.id for item in kb.evidence.list(output_id=record.id)] == [
            evidence.id
        ]

        rolled_back_id = uuid.uuid4()
        with pytest.raises(RuntimeError, match="rollback evidence"):
            with kb.transaction() as tx:
                tx.evidence.create(
                    evidence_id=rolled_back_id,
                    output_type="record",
                    output_id=record.id,
                    source_id=source_id,
                )
                raise RuntimeError("rollback evidence")
        assert kb.evidence.get(rolled_back_id) is None


def test_revision_one_database_upgrades_additively_without_losing_rows(tmp_path):
    with _client(tmp_path, "upgrade.db") as kb:
        kb.initialize()
        collection = kb.collections.create(name="Preserved during upgrade")

        # Simulate the prior portable release in this disposable test database.
        with kb.database.transaction() as session:
            projections_table.drop(session.connection())
            artifacts_table.drop(session.connection())
            evidence_table.drop(session.connection())
            jobs_table.drop(session.connection())
            schema_versions_table.drop(session.connection())
            session.execute(
                delete(schema_migrations_table).where(
                    schema_migrations_table.c.version.in_([2, 3, 4, 5, 6, 7, 8])
                )
            )

        assert kb.schema_version() == 1
        assert kb.initialize() == 8
        assert kb.collections.require(collection.id).name == "Preserved during upgrade"

        with kb.database.session() as session:
            tables = {
                row[0]
                for row in session.execute(
                    text(
                        "select name from sqlite_master "
                        "where type = 'table' and name like 'mkb_%'"
                    )
                )
            }
        assert {
            "mkb_artifacts",
            "mkb_projections",
            "mkb_evidence",
            "mkb_jobs",
            "mkb_extraction_schema_versions",
        }.issubset(tables)
