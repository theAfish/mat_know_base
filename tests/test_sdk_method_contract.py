import inspect
import uuid

import pytest

from mkb import (
    ConflictError,
    KnowledgeBase,
    NotFoundError,
    Parser,
    Pipeline,
    Step,
    ValidationError,
)
from mkb.graph import Graph
from mkb.pipelines import Pipelines
from mkb.registries import Parsers, Steps
from mkb.repositories import (
    Artifacts,
    Collections,
    ExtractionSchemas,
    Projections,
    Records,
    Sources,
)


SUPPORTED_METHODS = {
    Collections: {"create", "get", "list", "require"},
    Sources: {
        "add_bytes",
        "add_text",
        "content_exists",
        "get",
        "list",
        "open",
        "read_bytes",
        "require",
    },
    Artifacts: {
        "add_bytes",
        "content_exists",
        "get",
        "list",
        "open",
        "read_bytes",
        "require",
    },
    Records: {"create", "export_json", "get", "get_for_collection", "list", "require"},
    ExtractionSchemas: {"create", "get", "list", "register", "require"},
    Projections: {"create", "export_json", "get", "list", "require"},
    Graph: {
        "get_entity",
        "get_relation",
        "list_entities",
        "list_relations",
        "neighbors",
        "require_entity",
        "upsert_entity",
        "upsert_relation",
    },
    Pipelines: {"get", "get_run", "list", "register", "require", "run"},
    Parsers: {"for_source_type", "get", "list", "parse", "register", "require"},
    Steps: {"get", "list", "register", "require"},
}


def test_supported_grouped_method_inventory_is_deliberate():
    for service_type, expected in SUPPORTED_METHODS.items():
        actual = {
            name
            for name, member in inspect.getmembers(service_type, inspect.isfunction)
            if not name.startswith("_")
        }
        assert actual == expected


def test_repository_method_success_optional_lookup_errors_and_serialization(tmp_path):
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'contract.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
    )
    with kb:
        assert kb.initialize() == kb.initialize()
        missing = uuid.uuid4()
        optional_services = (
            (kb.collections, "Collection"),
            (kb.sources, "Source"),
            (kb.artifacts, "Artifact"),
            (kb.records, "Record"),
            (kb.projections, "Projection"),
        )
        for service, label in optional_services:
            assert service.get(missing) is None
            with pytest.raises(NotFoundError, match=label):
                service.require(missing)
        assert kb.schemas.get(missing) is None
        with pytest.raises(NotFoundError, match="schema"):
            kb.schemas.require(missing)

        collection = kb.collections.create(name="Contract")
        source = kb.sources.add_text(collection.id, "evidence", filename="notes.txt")
        artifact = kb.artifacts.add_bytes(
            source.id,
            b"derived",
            processing_type="TEXT",
            format="txt",
        )
        record = kb.records.create(collection_id=collection.id, data={"value": 1})
        schema = kb.schemas.register(
            name="contract-schema",
            domain="test",
            definition={"type": "object"},
            system_prompt="Extract.",
        )
        projection = kb.projections.create(
            schema_id=schema.id,
            record_id=record.id,
            data={"value": 1},
        )

        assert [item.id for item in kb.collections.list(limit=1)] == [collection.id]
        assert [item.id for item in kb.sources.list(collection_id=collection.id)] == [source.id]
        assert kb.sources.content_exists(source.id) is True
        with kb.sources.open(source.id) as stream:
            assert stream.read() == b"evidence"
        assert kb.sources.read_bytes(source.id) == b"evidence"
        assert [item.id for item in kb.artifacts.list(source_id=source.id)] == [artifact.id]
        assert kb.artifacts.content_exists(artifact.id) is True
        with kb.artifacts.open(artifact.id) as stream:
            assert stream.read() == b"derived"
        assert kb.artifacts.read_bytes(artifact.id) == b"derived"
        assert kb.records.get_for_collection(collection.id).id == record.id
        assert '"value": 1' in kb.records.export_json()
        assert [item.id for item in kb.schemas.list()] == [schema.id]
        assert [item.id for item in kb.projections.list(record_id=record.id)] == [projection.id]
        assert '"value": 1' in kb.projections.export_json()

        with pytest.raises(ConflictError, match="already exists"):
            kb.schemas.register(
                name="contract-schema",
                domain="test",
                definition={},
                system_prompt="Extract.",
            )
        with pytest.raises(ValidationError, match="limit"):
            kb.collections.list(limit=0)


def test_graph_registry_and_pipeline_method_contracts():
    kb = KnowledgeBase.from_url(database_url="sqlite:///:memory:")
    with kb:
        missing = uuid.uuid4()
        assert kb.graph.get_entity(missing) is None
        assert kb.graph.get_relation(missing) is None
        with pytest.raises(NotFoundError, match="Entity"):
            kb.graph.require_entity(missing)

        left = kb.graph.upsert_entity(type="node", name="left")
        right = kb.graph.upsert_entity(type="node", name="right")
        relation = kb.graph.upsert_relation(left.id, right.id, type="linked")
        assert {item.id for item in kb.graph.list_entities(type="node")} == {
            left.id,
            right.id,
        }
        assert kb.graph.list_relations(entity_id=left.id) == [relation]
        assert kb.graph.neighbors(left.id) == [right]

        parser = Parser(
            name="plain",
            source_types=frozenset({"text"}),
            handler=lambda _context, content: content.decode(),
        )
        step = Step(name="noop", handler=lambda _context, _state: {})
        pipeline = Pipeline(name="contract", steps=(step,))
        assert kb.parsers.register(parser) is parser
        assert kb.parsers.get("plain") is parser
        assert kb.parsers.require("plain") is parser
        assert kb.parsers.list() == [parser]
        assert kb.parsers.for_source_type("text") == [parser]
        assert kb.parsers.parse("plain", b"value", source_type="text") == "value"
        assert kb.steps.register(step) is step
        assert kb.steps.get("noop") is step
        assert kb.steps.require("noop") is step
        assert kb.steps.list() == [step]
        assert kb.pipelines.register(pipeline) is pipeline
        assert kb.pipelines.get("contract") is pipeline
        assert kb.pipelines.require("contract") is pipeline
        assert kb.pipelines.list() == [pipeline]
        run = kb.pipelines.run("contract")
        assert kb.pipelines.get_run(run.id) == run

        for registry, name in (
            (kb.parsers, "missing-parser"),
            (kb.steps, "missing-step"),
            (kb.pipelines, "missing-pipeline"),
        ):
            assert registry.get(name) is None
            with pytest.raises(NotFoundError):
                registry.require(name)
