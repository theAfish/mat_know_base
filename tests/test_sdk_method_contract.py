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
from mkb.application_services import AssistantService, MaintenanceService, SettingsService
from mkb.job_service import Jobs
from mkb.managed_services import Feedback, PostProcessors, Skills
from mkb.materials import (
    MaterialFeedback,
    MaterialFrames,
    MaterialGraph,
    MaterialLibrary,
    MaterialProjects,
    MaterialProjections,
    MaterialSpaces,
    MaterialWorkflows,
)
from mkb.pipelines import Pipelines
from mkb.registries import Parsers, Steps
from mkb.repositories import (
    Artifacts,
    CollectionGroups,
    Collections,
    EvidenceLinks,
    ExtractionSchemas,
    Projections,
    Records,
    Sources,
    Workflows,
)


SUPPORTED_METHODS = {
    Collections: {
        "assign_group",
        "create",
        "delete",
        "get",
        "list",
        "require",
        "update",
    },
    CollectionGroups: {"assign", "create", "delete", "get", "list", "require", "update"},
    Sources: {
        "add_bytes",
        "add_directory",
        "add_file",
        "add_records",
        "add_text",
        "add_uri",
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
        "register",
        "require",
    },
    Records: {
        "create",
        "evidence",
        "export_json",
        "get",
        "get_for_collection",
        "list",
        "query",
        "require",
    },
    ExtractionSchemas: {
        "create",
        "delete",
        "get",
        "get_version",
        "history",
        "list",
        "register",
        "require",
        "require_version",
        "update",
    },
    Projections: {"create", "export_json", "get", "list", "require"},
    EvidenceLinks: {"create", "get", "list", "require"},
    Workflows: {"get", "list", "require"},
    Graph: {
        "extract",
        "get_entity",
        "get_relation",
        "list_entities",
        "list_relations",
        "list_reviews",
        "neighbors",
        "query",
        "review",
        "require_entity",
        "traverse",
        "upsert_entity",
        "upsert_relation",
    },
    Pipelines: {
        "get",
        "get_run",
        "list",
        "register",
        "require",
        "resume",
        "run",
        "submit",
    },
    Parsers: {
        "for_source_type",
        "get",
        "list",
        "parse",
        "register",
        "register_adapter",
        "require",
    },
    Steps: {"get", "list", "register", "require"},
    Jobs: {
        "available",
        "cancel",
        "cancel_all",
        "events",
        "find_active",
        "get",
        "list",
        "recover_interrupted",
        "require",
        "submit",
        "submit_action",
        "wait",
    },
    SettingsService: {"inspect", "runtime", "update", "validate_startup"},
    AssistantService: {"chat"},
    MaintenanceService: {
        "backup_metadata",
        "cleanup",
        "cleanup_plan",
        "compare_inventories",
        "inventory",
        "migration_inventory",
        "reconcile",
        "restore_missing_artifact",
        "verify_content",
    },
    Feedback: {"create", "get", "list", "require", "resolve", "review"},
    Skills: {"create", "delete", "get", "import_files", "list", "require"},
    PostProcessors: {"delete", "get", "list", "register", "require"},
    MaterialFrames: {"get", "history", "list"},
    MaterialSpaces: {"create", "delete", "get", "import_file", "list", "update"},
    MaterialProjections: {
        "export_projection",
        "export_space",
        "export",
        "export_all",
        "delete",
        "get",
        "list",
        "review",
        "review_all",
        "run",
        "run_all",
    },
    MaterialGraph: {"clear", "extract", "get", "review", "review_counts"},
    MaterialFeedback: {"list", "resolve", "review", "summary"},
    MaterialLibrary: {"link_processed", "search"},
    MaterialProjects: {
        "assign_group",
        "create_group",
        "delete",
        "delete_group",
        "list",
        "list_assets",
        "list_groups",
        "list_processed_assets",
        "rename",
        "update_group",
    },
    MaterialWorkflows: {
        "correct",
        "curate_schema",
        "get",
        "get_canonical",
        "get_raw",
        "list",
        "list_canonical",
        "list_raw",
        "list_schema_proposals",
        "list_tasks",
        "rebuild_indexes",
        "readiness",
        "require",
        "review",
        "review_schema_proposal",
        "edit_schema_proposal",
        "run_task",
        "schedule_reextraction",
        "search",
        "schema_proposal_revisions",
        "schema_status",
        "delete_raw_version",
    },
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
        queried = kb.graph.query(entity_type="node", name_contains="left")
        traversed = kb.graph.traverse(left.id, max_depth=1, direction="out")
        assert queried.entities == (left,)
        assert queried.relations == (relation,)
        assert {item.id for item in traversed.entities} == {left.id, right.id}

        extracted = kb.graph.extract(
            "ignored by test extractor",
            extractor=lambda _content: {
                "entities": [
                    {"id": "sample", "type": "material", "name": "Steel"},
                    {"id": "phase", "type": "phase", "name": "Ferrite"},
                ],
                "relations": [
                    {
                        "source": "sample",
                        "target": "phase",
                        "type": "contains",
                    }
                ],
            },
        )
        assert len(extracted.entities) == 2
        assert len(extracted.relations) == 1
        review = kb.graph.review(
            extracted.entities[0].id,
            reviewer=lambda _target: {
                "decision": "modified",
                "changes": {"properties": {"reviewed": True}},
                "notes": "Verified",
            },
        )
        assert review.decision == "modified"
        assert kb.graph.require_entity(review.target_id).properties["reviewed"] is True
        assert kb.graph.list_reviews(target_id=review.target_id) == [review]

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


def test_graph_compatibility_operations_are_injected_and_typed():
    from mkb.adapters import InMemoryGraphStore

    state = {
        "graph": {
            "concepts": [{"label": "Steel"}],
            "relations": [],
        }
    }
    calls = []
    graph = Graph(InMemoryGraphStore())
    graph._bind_compatibility(
        loader=lambda: {"graph": state["graph"]},
        extractor=lambda **kwargs: calls.append(("extract", kwargs)) or {"ok": True},
        reviewer=lambda **kwargs: calls.append(("review", kwargs)) or {"ok": True},
    )

    assert [entity.name for entity in graph.query().entities] == ["Steel"]
    extracted = graph.extract(collection_id=uuid.uuid4(), parameters={"model": "test"})
    reviewed = graph.review(parameters={"mode": "global"})

    assert extracted.metadata["operation_result"] == {"ok": True}
    assert reviewed.target_type == "graph"
    assert reviewed.metadata["operation_result"] == {"ok": True}
    assert [call[0] for call in calls] == ["extract", "review"]
