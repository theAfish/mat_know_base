from mkb.spaces.schema_utils import merge_field_descriptions_into_schema


def test_merge_field_descriptions_into_schema_appends_legacy_guidance():
    schema = {
        "templates": {
            "type": "list",
            "description": "Template rows.",
            "item_schema": {},
        },
        "conditions": {
            "type": "list",
            "item_schema": {},
        },
    }

    merged = merge_field_descriptions_into_schema(
        schema,
        {
            "templates": "Exclude controls.",
            "conditions": "Capture pH and temperature.",
        },
    )

    assert merged["templates"]["description"] == (
        "Template rows.\n\nExtraction guidance: Exclude controls."
    )
    assert merged["conditions"]["description"] == "Capture pH and temperature."
    assert schema["templates"]["description"] == "Template rows."
