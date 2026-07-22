"""Public-API-only SQLite/filesystem example for an installed base wheel."""

from pathlib import Path
import tempfile

from mkb import KnowledgeBase, Parser, Pipeline, Step


def main(root: Path | None = None) -> None:
    workspace = root or Path(tempfile.mkdtemp(prefix="mkb-quickstart-"))
    with KnowledgeBase.from_url(
        database_url=f"sqlite:///{workspace / 'knowledge.db'}",
        object_store_url=(workspace / "objects").resolve().as_uri(),
    ) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Portable example")
        source = kb.sources.add_text(collection.id, "Calcite is CaCO3.")
        file_path = workspace / "notes.txt"
        file_path.write_text("A local file source.")
        file_source = kb.sources.add_file(collection.id, file_path)
        structured_source = kb.sources.add_records(
            collection.id,
            [{"material": "calcite", "formula": "CaCO3"}],
            filename="materials.json",
        )
        schema = kb.schemas.register(
            name="material-record",
            domain="example",
            definition={"type": "object", "properties": {"formula": {"type": "string"}}},
            system_prompt="Normalize the material formula.",
        )

        parser = Parser(
            name="lines",
            source_types=frozenset({"text/plain"}),
            handler=lambda _context, content: content.decode().splitlines(),
        )
        kb.parsers.register(parser)
        normalize = Step(
            name="normalize",
            deterministic=True,
            handler=lambda _context, state: {"name": state["name"].strip().lower()},
        )
        def persist(context, state):
            record = context.knowledge_base.records.create(
                collection_id=collection.id,
                data={"material": state["name"], "formula": "CaCO3"},
            )
            context.knowledge_base.evidence.create(
                output_type="record",
                output_id=record.id,
                source_id=source.id,
                excerpt="Calcite is CaCO3.",
            )
            material = context.knowledge_base.graph.upsert_entity(
                type="material", name="Calcite"
            )
            formula = context.knowledge_base.graph.upsert_entity(
                type="formula", name="CaCO3"
            )
            context.knowledge_base.graph.upsert_relation(
                material.id, formula.id, type="has_formula"
            )
            return {
                "label": f"material:{state['name']}",
                "record_id": str(record.id),
                "material_id": str(material.id),
                "formula_id": str(formula.id),
            }

        annotate = Step(name="persist", deterministic=False, handler=persist)
        kb.steps.register(normalize)
        kb.steps.register(annotate)
        kb.pipelines.register(Pipeline(name="consumer-pipeline", steps=(normalize, annotate)))
        run = kb.pipelines.run("consumer-pipeline", inputs={"name": " Calcite "})

        assert run.outputs["label"] == "material:calcite"
        assert kb.sources.read_bytes(source.id) == b"Calcite is CaCO3."
        assert kb.sources.read_bytes(file_source.id) == b"A local file source."
        assert kb.sources.require(structured_source.id).metadata["record_count"] == 1
        record = kb.records.require(run.outputs["record_id"])
        assert kb.records.require(record.id).data["formula"] == "CaCO3"
        assert len(kb.evidence.list(output_id=record.id)) == 1
        assert kb.schemas.require(schema.id).name == "material-record"
        assert str(kb.graph.neighbors(run.outputs["material_id"])[0].id) == run.outputs[
            "formula_id"
        ]
        print(f"portable quickstart passed in {workspace}")


if __name__ == "__main__":
    main()
