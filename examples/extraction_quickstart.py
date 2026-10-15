"""Offline extraction example: explicit SQLite + file storage + demo model.

Requires mat-know-base[sql]. Replace the model callback and OCR parser with your
own providers when embedding this workflow in a real application.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from mkb import (
    CallableModelProvider,
    KnowledgeBase,
    ParsedContent,
    Parser,
    PostProcessorDefinition,
    SpaceDefinition,
)


def demo_model(prompt, *, parameters):
    assert "Calcite is CaCO3" in prompt
    assert parameters["space"] == "composition"
    return {"formula": " CaCO3 "}


def main():
    with TemporaryDirectory(prefix="mkb-extraction-") as directory:
        root = Path(directory)
        with KnowledgeBase.from_url(
            database_url=f"sqlite:///{root / 'knowledge.db'}",
            object_store_url=(root / "objects").as_uri(),
            model_provider=CallableModelProvider(demo_model, identity="offline-demo"),
        ) as kb:
            kb.initialize()
            kb.parsers.register(Parser(
                name="demo-ocr",
                source_types=frozenset({"application/pdf"}),
                handler=lambda context, content: ParsedContent(
                    text="Calcite is CaCO3.", metadata={"engine": "demo"}
                ),
            ))
            kb.extraction.register_post_processor(PostProcessorDefinition(
                name="strip-formula",
                handler=lambda context, data: {**data, "formula": data["formula"].strip()},
            ))
            space = kb.extraction.register_space(SpaceDefinition(
                name="composition",
                schema={
                    "type": "object",
                    "properties": {"formula": {"type": "string"}},
                    "required": ["formula"],
                    "additionalProperties": False,
                },
                system_prompt="Extract only the chemical formula supported by the source.",
                parser="demo-ocr",
                post_processors=("strip-formula",),
            ))
            collection = kb.collections.create(name="Example paper")
            source = kb.sources.add_bytes(
                collection.id, b"offline PDF placeholder", filename="paper.pdf",
                media_type="application/pdf",
            )
            result = kb.extraction.extract_source(source.id, space=space.id)
            assert result.data == {"formula": "CaCO3"}
            assert kb.projections.require(result.projection.id).data == result.data
            assert len(kb.evidence.list(output_id=result.projection.id)) == 1
            assert kb.artifacts.read_bytes(result.artifacts[0].id) == b"Calcite is CaCO3."
            print("extraction quickstart passed:", result.data)


if __name__ == "__main__":
    main()
