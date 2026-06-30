from types import SimpleNamespace

from mkb.spaces.registry import _normalize_post_processors, resolve_post_processor


def test_normalize_post_processors_builds_default_from_legacy_settings():
    processors = _normalize_post_processors(
        None,
        legacy_defaults={
            "review_prompt": "legacy prompt",
            "review_allow_search": True,
            "review_search_tools": ["uniprot"],
        },
    )

    assert processors == [
        {
            "id": "default",
            "name": "Default reviewer",
            "description": "General projection review and correction.",
            "prompt": "legacy prompt",
            "tool_groups": ["reading", "uniprot"],
            "enabled": True,
        }
    ]


def test_resolve_post_processor_selects_named_profile_without_forcing_reading():
    space = SimpleNamespace(
        review_prompt=None,
        review_allow_search=False,
        review_search_tools=["web"],
        post_processors=[
            {
                "id": "schema_only",
                "name": "Schema only",
                "prompt": "schema prompt",
                "tool_groups": [],
            },
            {
                "id": "sequence_enricher",
                "name": "Sequence enricher",
                "prompt": "sequence prompt",
                "tool_groups": ["uniprot"],
            },
        ],
    )

    selected = resolve_post_processor(space, "sequence_enricher")

    assert selected["id"] == "sequence_enricher"
    assert selected["prompt"] == "sequence prompt"
    assert selected["tool_groups"] == ["uniprot"]

