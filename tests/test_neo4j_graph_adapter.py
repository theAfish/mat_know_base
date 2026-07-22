import uuid

import pytest

from mkb.adapters import Neo4jGraphStore
from mkb.exceptions import BackendUnavailableError, ConflictError
from mkb.models import Entity, Relation
from mkb.ports import GraphStore


class _Result:
    def __init__(self, records=()):
        self.records = list(records)

    def consume(self):
        return None

    def single(self):
        return self.records[0] if self.records else None

    def __iter__(self):
        return iter(self.records)


class _Session:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, **parameters):
        self.driver.calls.append((" ".join(query.split()), parameters))
        return self.driver.results.pop(0) if self.driver.results else _Result()


class _Driver:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.databases = []
        self.closed = False

    def session(self, *, database):
        self.databases.append(database)
        return _Session(self)

    def close(self):
        self.closed = True


def _entity_record(entity):
    return {
        "id": str(entity.id),
        "type": entity.type,
        "name": entity.name,
        "properties_json": '{"formula": "CaCO3"}',
        "created_at": None,
        "updated_at": None,
    }


def _relation_record(relation):
    return {
        "id": str(relation.id),
        "source_id": str(relation.source_id),
        "target_id": str(relation.target_id),
        "type": relation.type,
        "properties_json": "{}",
        "created_at": None,
        "updated_at": None,
    }


def test_neo4j_adapter_conforms_without_importing_optional_driver():
    left = Entity(id=uuid.uuid4(), type="material", name="Calcite", properties={"formula": "CaCO3"})
    right = Entity(id=uuid.uuid4(), type="formula", name="CaCO3")
    relation = Relation(
        id=uuid.uuid4(), source_id=left.id, target_id=right.id, type="has_formula"
    )
    driver = _Driver(
        _Result(),
        _Result([_entity_record(left)]),
        _Result([_entity_record(left)]),
        _Result([{"matched": 1}]),
        _Result([_relation_record(relation)]),
        _Result([_relation_record(relation)]),
    )
    store = Neo4jGraphStore(driver=driver, database="knowledge")

    assert isinstance(store, GraphStore)
    assert store.upsert_entity(left) == left
    assert store.get_entity(left.id) == left
    assert store.list_entities(type="material") == [left]
    assert store.upsert_relation(relation) == relation
    assert store.get_relation(relation.id) == relation
    assert store.list_relations(entity_id=left.id, type="has_formula") == [relation]
    assert driver.databases == ["knowledge"] * 6
    assert driver.calls[0][1]["properties_json"] == '{"formula": "CaCO3"}'

    store.close()
    assert driver.closed is True
    with pytest.raises(BackendUnavailableError, match="closed"):
        store.list_entities()


def test_neo4j_adapter_rejects_relation_when_endpoints_are_missing():
    relation = Relation(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        target_id=uuid.uuid4(),
        type="missing",
    )
    store = Neo4jGraphStore(driver=_Driver(_Result([{"matched": 0}])))

    with pytest.raises(ConflictError, match="endpoints"):
        store.upsert_relation(relation)
