import pytest

from mkb import ConflictError, KnowledgeBase, Parser, Pipeline, Step, ValidationError


def test_parser_registry_is_per_client_and_passes_context():
    first = KnowledgeBase()
    second = KnowledgeBase()
    parser = Parser(
        name="line-parser",
        source_types=frozenset({"text/plain", ".TXT"}),
        handler=lambda context, content: {
            "client": context.knowledge_base,
            "lines": content.decode().splitlines(),
            "prefix": context.parameters["prefix"],
        },
    )

    first.parsers.register(parser)
    result = first.parsers.parse(
        "line-parser",
        b"one\ntwo",
        source_type="TEXT/PLAIN",
        parameters={"prefix": "sample"},
    )

    assert result == {"client": first, "lines": ["one", "two"], "prefix": "sample"}
    assert first.parsers.for_source_type(".txt") == [parser]
    assert second.parsers.get("line-parser") is None
    with pytest.raises(ConflictError, match="already registered"):
        first.parsers.register(parser)


def test_parser_contract_rejects_invalid_definitions_and_content():
    with pytest.raises(ValidationError, match="source type"):
        Parser(name="empty", handler=lambda _context, _content: None, source_types=frozenset())

    parser = Parser(
        name="text", handler=lambda _context, content: content, source_types=frozenset({"text"})
    )
    kb = KnowledgeBase()
    with pytest.raises(ValidationError, match="does not support"):
        kb.parsers.parse(parser, b"data", source_type="pdf")
    with pytest.raises(ValidationError, match="bytes"):
        kb.parsers.parse(parser, "data", source_type="text")


def test_registered_steps_compose_into_a_pipeline_without_package_edits():
    first = KnowledgeBase()
    second = KnowledgeBase()
    normalize = Step(
        name="normalize",
        deterministic=True,
        handler=lambda _context, state: {"value": state["value"].strip().lower()},
    )
    first.steps.register(normalize)
    pipeline = Pipeline(name="custom", steps=(first.steps.require("normalize"),))
    first.pipelines.register(pipeline)

    run = first.pipelines.run("custom", inputs={"value": "  Calcite "})

    assert run.outputs["value"] == "calcite"
    assert second.steps.get("normalize") is None


def test_portable_schema_registration_is_persisted_and_client_scoped(tmp_path):
    first = KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'first.db'}")
    second = KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'second.db'}")
    try:
        first.initialize()
        second.initialize()
        schema = first.schemas.register(
            name="consumer-schema",
            domain="general",
            definition={"type": "object", "properties": {"value": {"type": "string"}}},
            system_prompt="Extract supported values.",
        )

        reloaded = first.schemas.get(schema.id)
        assert reloaded is not None
        assert reloaded.id == schema.id
        assert reloaded.name == schema.name
        assert reloaded.definition == schema.definition
        assert second.schemas.get("consumer-schema") is None
    finally:
        first.close()
        second.close()
