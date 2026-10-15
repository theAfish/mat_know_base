"""Exercise extraction through host-owned repositories, OCR, and inference."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
import uuid

import pytest

from mkb.adapters import FileObjectStore
from mkb.exceptions import ConflictError, NotFoundError, ProviderError, ValidationError
from mkb.extraction import (
    CallableModelProvider,
    ParsedContent,
    PostProcessorDefinition,
    SpaceDefinition,
)
from mkb.models import Artifact, Collection, Evidence, ExtractionSchema, Projection, Record, Source
from mkb.registries import Parser
from mkb.sdk import KnowledgeBase
from mkb.storage import RepositoryBundle


class MemoryRepository:
    """Small test repository with public models and no SQL/session dependency."""

    def __init__(self, model, id_field, *, records=None, schemas=None):
        self.model = model
        self.id_field = id_field
        self.records = records
        self.schemas = schemas
        self.rows = {}
        self.fail_create = False

    def create(self, row=None, **values):
        if self.fail_create:
            raise RuntimeError("repository write failed")
        if row is not None:
            if isinstance(row, Source):
                row = row.model_copy(update={"collection_ids": (values["collection_id"],)})
        else:
            identifier = values.pop(self.id_field, None) or uuid.uuid4()
            values = {key: value for key, value in values.items() if value is not None}
            if self.model is ExtractionSchema and self.get_by_name(values["name"]):
                raise ConflictError("Schema already exists")
            if self.model is Projection:
                values["collection_id"] = self.records.get(values["record_id"]).collection_id
                values["schema_version"] = self.schemas.get(values["schema_id"]).version
            row = self.model(id=identifier, **values)
        self.rows[row.id] = row
        return row

    def get(self, identifier):
        return self.rows.get(uuid.UUID(str(identifier)))

    def get_by_name(self, name):
        return next((row for row in self.rows.values() if row.name == name), None)

    def list(self, *, limit=100, offset=0, **filters):
        rows = list(self.rows.values())
        for field, value in filters.items():
            if value is None or field in {"include_deleted", "include_history", "newest_only"}:
                continue
            if self.model is Source and field == "collection_id":
                rows = [row for row in rows if value in row.collection_ids]
            else:
                rows = [row for row in rows if getattr(row, field) == value]
        return rows[offset : offset + limit]

    def update(self, identifier, **changes):
        row = self.get(identifier)
        if self.model is ExtractionSchema:
            changes["version"] = row.version + 1
        row = row.model_copy(
            update={key: value for key, value in changes.items() if value is not None}
        )
        self.rows[row.id] = row
        return row


def _repositories():
    records = MemoryRepository(Record, "record_id")
    schemas = MemoryRepository(ExtractionSchema, "schema_id")
    return RepositoryBundle(
        collections=MemoryRepository(Collection, "collection_id"),
        sources=MemoryRepository(Source, "source_id"),
        artifacts=MemoryRepository(Artifact, "artifact_id"),
        records=records,
        schemas=schemas,
        projections=MemoryRepository(Projection, "projection_id", records=records, schemas=schemas),
        evidence=MemoryRepository(Evidence, "evidence_id"),
    )


def _client(tmp_path, provider=None, *, repositories=None, transactional=True):
    bundle = repositories or _repositories()

    @contextmanager
    def transaction():
        repos = [
            getattr(bundle, name)
            for name in (
                "collections",
                "sources",
                "artifacts",
                "records",
                "schemas",
                "projections",
                "evidence",
            )
        ]
        saved = [deepcopy(repo.rows) for repo in repos]
        try:
            yield bundle
        except Exception:
            for repo, rows in zip(repos, saved):
                repo.rows = rows
            raise

    return KnowledgeBase.from_repositories(
        replace(bundle, transaction=transaction if transactional else None),
        object_store=FileObjectStore(tmp_path),
        model_provider=provider,
    )


def _space(**kwargs):
    return SpaceDefinition(
        name="measurements",
        schema={
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        system_prompt="Extract the measured value.",
        **kwargs,
    )


def _source(kb, *, text="value = 12", filename="sample.txt"):
    collection = kb.collections.create(name="Research")
    return kb.sources.add_text(collection.id, text, filename=filename)


def test_ocr_projection_processors_and_provenance_without_database(tmp_path):
    calls = []

    def generate(prompt, *, parameters):
        calls.append((prompt, parameters))
        return '{"value": "12"}'

    with _client(tmp_path, CallableModelProvider(generate, identity="my-agent")) as kb:
        assert kb.database is None
        collection = kb.collections.create(name="Scanned documents")
        sources = [
            kb.sources.add_bytes(
                collection.id, b"fake scan", filename=f"page{i}.pdf", media_type="application/pdf"
            )
            for i in (1, 2)
        ]
        kb.parsers.register(
            Parser(
                name="ocr",
                source_types=frozenset({"application/pdf"}),
                version="3",
                handler=lambda context, content: ParsedContent(
                    text=f"value = 12 ({context.parameters['language']})",
                    metadata={"pages": 1, "input_size": len(content)},
                ),
            )
        )
        order = []

        def normalize(context, data):
            order.append("normalize")
            assert context.sources == tuple(sources)
            assert context.documents[0].metadata["pages"] == 1
            return {"value": float(data["value"])}

        def scale(context, data):
            order.append("scale")
            return {"value": data["value"] * context.parameters["factor"]}

        kb.extraction.register_post_processor(PostProcessorDefinition("normalize", normalize))
        kb.extraction.register_post_processor(PostProcessorDefinition("scale", scale, version="2"))
        space = kb.extraction.register_space(
            _space(parser="ocr", post_processors=("normalize", "scale"))
        )
        result = kb.extraction.extract(
            collection.id,
            space=space.id,
            parser_parameters={"language": "en"},
            model_parameters={"temperature": 0},
            post_processor_parameters={"scale": {"factor": 2}},
        )

        assert result.data == {"value": 24}
        assert order == ["normalize", "scale"]
        assert len(calls) == 1
        assert "value = 12 (en)" in calls[0][0]
        assert calls[0][1]["response_schema"] == space.definition
        assert calls[0][1]["temperature"] == 0
        assert result.projection.schema_version == space.version
        assert result.projection.validation["post_processors"][-1] == {
            "name": "scale",
            "version": "2",
        }
        assert (
            kb.records.require(result.record.id).data["documents"][0]["text"] == "value = 12 (en)"
        )
        assert len(result.artifacts) == len(result.evidence) == 2
        for source, artifact, evidence in zip(sources, result.artifacts, result.evidence):
            assert kb.artifacts.read_bytes(artifact.id) == b"value = 12 (en)"
            assert artifact.metadata["parser_version"] == "3"
            assert evidence.source_id == source.id
            assert evidence.artifact_id == artifact.id
            assert evidence.output_id == result.projection.id
            assert kb.evidence.require(evidence.id).locator == {"kind": "document"}
        assert json.loads(result.model_dump_json())["projection"]["data"] == {"value": 24}


@pytest.mark.parametrize("transactional", [True, False])
def test_text_extraction_and_space_definitions_survive_client_recreation(tmp_path, transactional):
    bundle = _repositories()
    provider = CallableModelProvider(lambda _prompt, **_: {"value": 12})
    with _client(tmp_path, provider, repositories=bundle, transactional=transactional) as first:
        source = _source(first)
        space = first.extraction.register_space(_space())
    with _client(tmp_path, provider, repositories=bundle, transactional=transactional) as second:
        result = second.extraction.extract_source(source.id, space=space.name)
        assert result.data == {"value": 12}
        assert result.artifacts[0].processing_type == "text"
        assert result.record.collection_id == source.collection_ids[0]


@pytest.mark.parametrize(
    "output, message",
    [
        ("not json", "valid JSON"),
        ({"value": "12"}, "does not match space"),
        ({"value": float("nan")}, "JSON-compatible"),
        ({"value": object()}, "JSON-compatible"),
    ],
)
def test_invalid_model_output_does_not_write_extraction_results(tmp_path, output, message):
    with _client(tmp_path, CallableModelProvider(lambda _prompt, **_: output)) as kb:
        source = _source(kb)
        kb.extraction.register_space(_space())
        with pytest.raises(ValidationError, match=message):
            kb.extraction.extract_source(source.id, space="measurements")
        assert (
            kb.records.list()
            == kb.projections.list()
            == kb.artifacts.list()
            == kb.evidence.list()
            == []
        )
        assert list(kb.object_store.list("processed")) == []


def test_provider_failure_is_typed_and_preserves_cause(tmp_path):
    def fail(*_args, **_kwargs):
        raise TimeoutError("offline")

    with _client(tmp_path, CallableModelProvider(fail)) as kb:
        source = _source(kb)
        kb.extraction.register_space(_space())
        with pytest.raises(ProviderError) as captured:
            kb.extraction.extract_source(source.id, space="measurements")
        assert isinstance(captured.value.__cause__, TimeoutError)
        assert kb.artifacts.list() == []


def test_processor_registry_is_client_scoped_and_required_before_inference(tmp_path):
    called = []
    provider = CallableModelProvider(lambda *_args, **_kwargs: called.append(True))
    with (
        _client(tmp_path / "first", provider) as first,
        _client(tmp_path / "second", provider) as second,
    ):
        processor = PostProcessorDefinition("normalize", lambda _context, data: data)
        first.extraction.register_post_processor(processor)
        with pytest.raises(ConflictError, match="already registered"):
            first.extraction.register_post_processor(processor)
        assert second.extraction.post_processors.get("normalize") is None
        source = _source(second)
        second.extraction.register_space(_space(post_processors=("normalize",)))
        with pytest.raises(NotFoundError, match="not registered"):
            second.extraction.extract_source(source.id, space="measurements")
        assert called == []


def test_processor_failure_and_invalid_final_output_do_not_persist(tmp_path):
    with _client(tmp_path, CallableModelProvider(lambda *_args, **_kwargs: {"value": 1})) as kb:
        source = _source(kb)
        kb.extraction.register_space(_space(post_processors=("normalize",)))

        def fail(_context, _data):
            raise RuntimeError("cannot normalize")

        kb.extraction.register_post_processor(PostProcessorDefinition("normalize", fail))
        with pytest.raises(RuntimeError, match="cannot normalize"):
            kb.extraction.extract_source(source.id, space="measurements")
        kb.extraction.register_post_processor(
            PostProcessorDefinition("normalize", lambda _context, _data: {"wrong": 1}), replace=True
        )
        with pytest.raises(ValidationError, match="does not match space"):
            kb.extraction.extract_source(source.id, space="measurements")
        assert kb.records.list() == kb.artifacts.list() == []


def test_persistence_failure_rolls_back_records_and_artifact_objects(tmp_path):
    bundle = _repositories()
    with _client(
        tmp_path, CallableModelProvider(lambda *_args, **_kwargs: {"value": 1}), repositories=bundle
    ) as kb:
        source = _source(kb)
        kb.extraction.register_space(_space())
        bundle.evidence.fail_create = True
        with pytest.raises(RuntimeError, match="repository write failed"):
            kb.extraction.extract_source(source.id, space="measurements")
        assert kb.records.list() == kb.projections.list() == kb.artifacts.list() == []
        assert list(kb.object_store.list("processed")) == []
        assert kb.sources.read_bytes(source.id) == b"value = 12"


def test_source_selection_validates_membership_duplicates_and_missing_ocr(tmp_path):
    with _client(tmp_path, CallableModelProvider(lambda *_args, **_kwargs: {"value": 1})) as kb:
        source = _source(kb)
        other = kb.collections.create(name="Other")
        kb.extraction.register_space(_space())
        with pytest.raises(ValidationError, match="does not belong"):
            kb.extraction.extract(other.id, space="measurements", source_ids=[source.id])
        with pytest.raises(ValidationError, match="duplicates"):
            kb.extraction.extract(
                source.collection_ids[0], space="measurements", source_ids=[source.id, source.id]
            )
        with pytest.raises(ValidationError, match="at least one source"):
            kb.extraction.extract(other.id, space="measurements")
        pdf = kb.sources.add_bytes(
            other.id, b"pdf", filename="scan.pdf", media_type="application/pdf"
        )
        with pytest.raises(NotFoundError, match="OCR/content parser"):
            kb.extraction.extract_source(pdf.id, space="measurements")


def test_schema_change_during_inference_cannot_mislabel_projection_version(tmp_path):
    with _client(tmp_path) as kb:
        source = _source(kb)
        space = kb.extraction.register_space(_space())

        def change_space(*_args, **_kwargs):
            kb.schemas.update(space.id, system_prompt="Updated prompt")
            return {"value": 1}

        kb.model_provider = CallableModelProvider(change_space)
        with pytest.raises(ConflictError, match="Space changed"):
            kb.extraction.extract_source(source.id, space=space.id)
        assert kb.artifacts.list() == kb.records.list() == []


def test_json_schema_validation_and_local_references(tmp_path):
    with pytest.raises(ValidationError, match="Invalid space JSON Schema"):
        SpaceDefinition("broken", {"type": "nonsense"}, "Extract")
    with pytest.raises(ValidationError, match="reserved"):
        SpaceDefinition("broken", {"x-mkb": {}}, "Extract")
    with _client(tmp_path, CallableModelProvider(lambda *_args, **_kwargs: {"value": 1})) as kb:
        source = _source(kb)
        kb.extraction.register_space(
            SpaceDefinition(
                "local-ref",
                {"$defs": {"output": _space().schema}, "$ref": "#/$defs/output"},
                "Extract",
            )
        )
        assert kb.extraction.extract_source(source.id, space="local-ref").data == {"value": 1}
        kb.extraction.register_space(
            SpaceDefinition(
                "remote-ref", {"$ref": "https://example.invalid/schema.json"}, "Extract"
            )
        )
        with pytest.raises(ValidationError, match="unresolved schema reference"):
            kb.extraction.extract_source(source.id, space="remote-ref")


def test_sql_adapter_uses_the_same_extraction_workflow(tmp_path):
    pytest.importorskip("sqlalchemy")
    with KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'extraction.db'}",
        object_store_url=f"file://{tmp_path / 'objects'}",
        model_provider=CallableModelProvider(lambda *_args, **_kwargs: {"value": 12}),
    ) as kb:
        kb.initialize()
        source = _source(kb)
        kb.extraction.register_space(_space())
        result = kb.extraction.extract_source(source.id, space="measurements")
        assert kb.projections.require(result.projection.id).data == {"value": 12}
        assert kb.artifacts.read_bytes(result.artifacts[0].id) == b"value = 12"
        kb.extraction.register_space(replace(_space(), name="another-space"))
        another = kb.extraction.extract_source(source.id, space="another-space")
        repeated = kb.extraction.extract_source(source.id, space="measurements")
        assert len({result.record.id, another.record.id, repeated.record.id}) == 3
        assert len(kb.records.list(collection_id=source.collection_ids[0])) == 3
        assert kb.projections.require(result.projection.id).data == {"value": 12}


def test_extraction_methods_reject_closed_clients(tmp_path):
    kb = _client(tmp_path, CallableModelProvider(lambda *_args, **_kwargs: {"value": 1}))
    source = _source(kb)
    kb.extraction.register_space(_space())
    kb.close()
    for call in (
        lambda: kb.extraction.register_space(replace(_space(), name="another")),
        lambda: kb.extraction.register_post_processor(
            PostProcessorDefinition("p", lambda _c, d: d)
        ),
        lambda: kb.extraction.extract_source(source.id, space="measurements"),
        lambda: kb.extraction.extract(source.collection_ids[0], space="measurements"),
    ):
        with pytest.raises(RuntimeError, match="closed"):
            call()
