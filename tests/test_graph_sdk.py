import uuid

import pytest

from mkb import ConflictError, Entity, Evidence, KnowledgeBase, Relation
from mkb.adapters import InMemoryGraphStore
from mkb.graph import Graph


def test_public_graph_models_have_stable_json_serialization():
    entity_id = uuid.uuid4()
    source_id = uuid.uuid4()
    entity = Entity(id=entity_id, type="material", name="Calcite")
    relation = Relation(
        id=uuid.uuid4(),
        source_id=entity_id,
        target_id=uuid.uuid4(),
        type="contains",
    )
    evidence = Evidence(
        id=uuid.uuid4(),
        output_type="entity",
        output_id=entity_id,
        source_id=source_id,
        locator={"page": 4},
    )

    assert entity.model_dump(mode="json")["id"] == str(entity_id)
    assert relation.model_dump(mode="json")["source_id"] == str(entity_id)
    assert evidence.model_dump(mode="json")["locator"] == {"page": 4}


def test_in_memory_graph_upsert_query_and_traversal():
    graph = Graph(InMemoryGraphStore())
    calcite_id = uuid.uuid4()
    paper_id = uuid.uuid4()
    calcite = graph.upsert_entity(
        entity_id=calcite_id, type="material", name="Calcite", properties={"formula": "CaCO3"}
    )
    paper = graph.upsert_entity(entity_id=paper_id, type="source", name="Paper")
    relation = graph.upsert_relation(
        paper.id, calcite.id, type="mentions", properties={"page": 2}
    )

    updated = graph.upsert_entity(
        entity_id=calcite_id, type="material", name="Calcite", properties={"formula": "CaCO₃"}
    )

    assert updated.id == calcite.id
    assert updated.created_at == calcite.created_at
    assert graph.list_entities(type="material") == [updated]
    assert graph.list_relations(entity_id=paper.id) == [relation]
    assert graph.neighbors(paper.id) == [updated]


def test_relation_requires_existing_endpoints():
    graph = Graph(InMemoryGraphStore())
    source = graph.upsert_entity(type="source", name="Known")

    with pytest.raises(ConflictError, match="endpoints"):
        graph.upsert_relation(source.id, uuid.uuid4(), type="mentions")


def test_portable_client_gets_an_isolated_graph_service(tmp_path):
    first = KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'first.db'}")
    second = KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'second.db'}")
    try:
        entity = first.graph.upsert_entity(type="material", name="Calcite")

        assert first.graph.get_entity(entity.id) == entity
        assert second.graph.get_entity(entity.id) is None
        assert "graph_traversal" in first.pipelines.capabilities
    finally:
        first.close()
        second.close()
