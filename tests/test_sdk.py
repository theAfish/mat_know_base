from types import SimpleNamespace

import pytest
from sqlalchemy import text

from mkb import (
    KnowledgeBase,
    MKBConfig,
    MKBError,
    Pipeline,
    Pipelines,
    Step,
    Steps,
    ValidationError,
)
from mkb.adapters import InMemoryGraphStore
from mkb.adapters import FileObjectStore, SQLAlchemyDatabase
from mkb.registries import Parsers


def _services(calls, identity):
    def operation(name, result=None):
        def invoke(*args, **kwargs):
            calls.append((identity, name, args, kwargs))
            return result

        return invoke

    return SimpleNamespace(
        ingest=operation("ingest", {"project_id": identity}),
        sync=operation("sync", {}),
        sync_project=operation("sync_project", {}),
        process=operation("process", {}),
        extract=operation("extract", {}),
        list_projects=operation("list_projects", [{"project_id": identity}]),
        list_assets=operation("list_assets", []),
        list_processed_assets=operation("list_processed_assets", []),
        list_frames=operation("list_frames", []),
        get_frame=operation("get_frame"),
        create_space=operation("create_space", {}),
        get_space=operation("get_space"),
        list_spaces=operation("list_spaces", []),
        project=operation("project", {}),
        get_projection=operation("get_projection"),
        list_projections=operation("list_projections", []),
    )


def test_clients_keep_service_bindings_and_configuration_isolated():
    calls = []
    first = KnowledgeBase(
        services=_services(calls, "first"),
        config=MKBConfig(database_url="sqlite:///first.db"),
    )
    second = KnowledgeBase(
        services=_services(calls, "second"),
        config=MKBConfig(database_url="sqlite:///second.db"),
    )

    assert first.ingest("a")["project_id"] == "first"
    assert second.list_projects()[0]["project_id"] == "second"
    assert first.config.database_url == "sqlite:///first.db"
    assert second.config.database_url == "sqlite:///second.db"
    assert [call[0] for call in calls] == ["first", "second"]


def test_client_delegates_lifecycle_arguments_explicitly():
    calls = []
    kb = KnowledgeBase(services=_services(calls, "kb"))

    kb.process(project_id="project", progress_callback="callback")

    assert calls[-1] == (
        "kb",
        "process",
        (),
        {"project_id": "project", "progress_callback": "callback"},
    )


def test_client_context_closes_and_rejects_further_calls():
    calls = []
    with KnowledgeBase(services=_services(calls, "kb")) as kb:
        assert kb.closed is False
    assert kb.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        kb.list_projects()


def test_client_owns_and_closes_injected_resources():
    calls = []

    class Resource:
        closed = False

        def close(self):
            self.closed = True

    database = Resource()
    object_store = Resource()
    kb = KnowledgeBase(
        services=_services(calls, "kb"),
        database=database,
        object_store=object_store,
    )

    kb.close()

    assert database.closed is True
    assert object_store.closed is True


def test_from_url_builds_isolated_database_object_store_and_pipeline_registry(tmp_path):
    first = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'first.db'}",
        object_store_url=(tmp_path / "first-objects").as_uri(),
    )
    second = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'second.db'}",
        object_store_url=(tmp_path / "second-objects").as_uri(),
    )
    pipeline = Pipeline(name="first-only", steps=(Step(name="one", handler=lambda _c, _s: {}),))
    try:
        first.database.check()
        second.database.check()
        first.object_store.put_bytes("raw", "example.txt", b"first")
        second.object_store.put_bytes("raw", "example.txt", b"second")
        first.pipelines.register(pipeline)

        assert first.object_store.get_bytes("raw", "example.txt") == b"first"
        assert second.object_store.get_bytes("raw", "example.txt") == b"second"
        assert first.pipelines.get("first-only") is pipeline
        assert second.pipelines.get("first-only") is None
        assert first.database is not second.database
        with pytest.raises(MKBError, match="explicitly configured"):
            first.list_projects()
    finally:
        first.close()
        second.close()


def test_from_url_rejects_invalid_configuration(tmp_path):
    with pytest.raises(ValidationError, match="database_url"):
        KnowledgeBase.from_url(database_url="")
    with pytest.raises(ValidationError, match="file or s3"):
        KnowledgeBase.from_url(
            database_url=f"sqlite:///{tmp_path / 'invalid.db'}",
            object_store_url="ftp://example.test/data",
        )


def test_from_url_configures_all_storage_buckets_and_validates_s3_raw_bucket(tmp_path):
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'buckets.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
        raw_bucket="inputs",
        processed_bucket="derived",
        archive_bucket="history",
        temp_bucket="scratch",
    )
    try:
        assert kb.config.raw_bucket == "inputs"
        assert kb.config.processed_bucket == "derived"
        assert kb.config.archive_bucket == "history"
        assert kb.config.temp_bucket == "scratch"
    finally:
        kb.close()

    with pytest.raises(ValidationError, match="raw_bucket must match"):
        KnowledgeBase.from_url(
            database_url=f"sqlite:///{tmp_path / 's3.db'}",
            object_store_url="s3://inputs?endpoint=http://localhost:9000",
            raw_bucket="different",
        )


def test_from_url_injects_owned_resources_registries_and_capabilities(tmp_path):
    class Resource:
        def __init__(self, capabilities):
            self.capabilities = frozenset(capabilities)
            self.closed = False

        def close(self):
            self.closed = True

    class GraphResource(InMemoryGraphStore):
        def __init__(self):
            super().__init__()
            self.closed = False

        def close(self):
            self.closed = True
            super().close()

    graph_store = GraphResource()
    model_provider = Resource({"model_generation"})
    model_provider.identity = "test/provider"
    job_backend = Resource({"durable_submission"})
    injected_steps = Steps()
    bindings = {}

    def parser_factory(kb):
        bindings["parser_kb"] = kb
        return Parsers(kb)

    def pipeline_factory(kb, capabilities):
        bindings["pipeline_kb"] = kb
        bindings["capabilities"] = capabilities
        return Pipelines(kb, capabilities=capabilities)

    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'injected.db'}",
        graph_store=graph_store,
        model_provider=model_provider,
        job_backend=job_backend,
        parser_registry_factory=parser_factory,
        pipeline_registry_factory=pipeline_factory,
        steps=injected_steps,
    )

    assert kb.graph_store is graph_store
    assert kb.model_provider is model_provider
    assert kb.job_backend is job_backend
    assert kb.steps is injected_steps
    assert bindings["parser_kb"] is kb
    assert bindings["pipeline_kb"] is kb
    assert {"transactions", "graph_traversal", "model_generation", "durable_submission"} <= (
        bindings["capabilities"]
    )

    kb.close()

    assert graph_store.closed is True
    assert model_provider.closed is True
    assert job_backend.closed is True


def test_explicit_content_operations_use_the_owning_client_resources(tmp_path):
    first_database = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'legacy-first.db'}")
    second_database = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'legacy-second.db'}")
    first_store = FileObjectStore(tmp_path / "legacy-first-objects")
    second_store = FileObjectStore(tmp_path / "legacy-second-objects")
    for database, value in ((first_database, "first"), (second_database, "second")):
        with database.transaction() as session:
            session.execute(text("create table identity (value text not null)"))
            session.execute(text("insert into identity values (:value)"), {"value": value})

    def probe(database, object_store):
        with database.session() as session:
            identity = session.execute(text("select value from identity")).scalar_one()
        object_store.put_bytes("raw", "identity.txt", identity.encode())
        return identity, object_store.get_bytes("raw", "identity.txt")

    try:
        assert probe(first_database, first_store) == ("first", b"first")
        assert probe(second_database, second_store) == ("second", b"second")
        assert first_store.get_bytes("raw", "identity.txt") == b"first"
        assert second_store.get_bytes("raw", "identity.txt") == b"second"
    finally:
        first_store.close()
        second_store.close()
        first_database.close()
        second_database.close()
