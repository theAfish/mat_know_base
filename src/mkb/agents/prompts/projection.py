"""
Projection prompt builder.

Dynamically constructs the projection agent prompt from a Space definition.
"""

from __future__ import annotations

import json


def build_projection_prompt(
    domain: str,
    system_prompt: str,
    extraction_schema: dict,
    field_descriptions: dict,
) -> str:
    """Build a projection prompt from space components.

    Args:
        domain: Research domain name.
        system_prompt: Domain-specific instructions.
        extraction_schema: JSON schema defining what fields to extract.
        field_descriptions: Per-field extraction guidance.

    Returns:
        Complete prompt string for the projection agent.
    """
    schema_str = json.dumps(extraction_schema, indent=2)
    field_desc_str = "\n".join(f"- **{k}**: {v}" for k, v in field_descriptions.items())

    return f"""\
You are a structured data extraction agent. You are given a knowledge frame — a comprehensive summary of a research paper — and must extract specific structured data according to the schema below.

Domain: {domain}

{system_prompt}

---

# Required Output Schema

```json
{schema_str}
```

# Field Guidance

{field_desc_str}

---

# Workflow

1. Call `get_frame_content` to read the knowledge frame content
2. Analyze the frame systematically, field by field
3. Extract data matching each field in the schema
4. For required fields where data is not found, set the value to null and note the gap in your assessment
5. Call `save_projection` with the extracted data and your confidence assessment
6. If any data is unclear, ambiguous, or potentially missing from the knowledge frame, call `flag_for_feedback` to request clarification

---

# Guidelines

- Extract ONLY from the knowledge frame content — do not fabricate data
- Preserve numerical precision — do not round values
- Include units wherever applicable
- If a field has multiple possible values and the schema says `type: list`, keep them as a JSON array internally
- Use role fields carefully: mark controls, comparisons, and background references explicitly rather than treating them as primary extracted entities
- For each row-like extracted record, include `is_core_study_data: true` when it represents the main material/data/function being investigated; set it to `false` for complementary, control, comparison, validation, or testing-only entries
- If `evidence_level` is present, assign the highest supported level using this rubric: 1 = in vivo functional validation, 2 = in vitro direct mineralization experiment, 3 = indirect experimental evidence, 4 = prediction/hypothesis/inference
- Note confidence level in your agent_notes for fields where the mapping is uncertain
- Flag rather than guess — when in doubt, use flag_for_feedback
"""
