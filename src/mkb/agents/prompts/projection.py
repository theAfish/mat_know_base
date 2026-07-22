"""
Projection prompt builder.

Dynamically constructs the projection agent prompt from a Space definition.
"""

from __future__ import annotations

import json


_QA_BENCHMARK_ADDENDUM = """

---

# Extra rules for `qa_benchmark` (mat_agent_bench format)

You are producing items that will be exported one-per-YAML into a
mat_agent_bench `question_bank/<capability>/<id>.yaml` file. Each
`questions[i]` MUST be runnable in complete isolation from the others.

Mandatory isolation checks before calling `save_projection`:
- No item references another (no "as in question X", no shared workspace assumptions).
- Every file the agent needs at runtime is listed in this item's own `data_files`.
- `reference_answers` must be checkable from ONLY this item's deliverables.
- `id` follows `<CAP>_<short_domain>_<NNN>_<YYYYMMDD>`, is unique within the bank,
  and matches the chosen `capability` prefix
  (IG=input_generation, SR=structure_retrieval, SC=structure_construction,
  WF=workflow_orchestration, BP=batch_processing, DD=data_diagnosis,
  EC=execution_contract, SA=scientific_analysis, SF=safety_refusal).
- Every non-budget `reference_answers[i].key` has a matching `scoring_checklist[i].id`.
- The four efficiency items are always present at the end of `scoring_checklist`:
  `turn_budget`, `no_retries`, `duration_budget`, `token_budget_total`, with
  corresponding entries in `reference_answers` (`{"max": <int>}` for the budgets).
- Always include one `grounding_source` reference_answer pointing at the seed
  data file plus an `llm_binary_judge` checklist item that verifies grounding.

Quality bar:
- Prefer cheap verifiers (`text_file_contains_all`, `text_file_regex`,
  `text_file_numeric_range`, `artifact_exists`) over `llm_binary_judge`.
- Prefix each `criterion` with `[Must]`, `[Suggested]`, or `[Variable]`.
- If the frame does not contain enough material for a high-quality task,
  emit `questions: []` rather than fabricate one.
"""


def build_projection_prompt(
    domain: str,
    system_prompt: str,
    extraction_schema: dict,
    field_descriptions: dict | None = None,
    purpose: str | None = None,
    source_type: str = "frame",
    source_id: str | None = None,
    project_id: str | None = None,
) -> str:
    """Build a projection prompt from space components.

    Args:
        domain: Research domain name.
        system_prompt: Domain-specific instructions.
        extraction_schema: JSON schema defining what fields to extract. Field
            guidance should live in schema ``description`` values.
        field_descriptions: Legacy guidance field. Ignored by the prompt.
        purpose: Space purpose (tabular_database | qa_benchmark | skill_cards | freeform).
            When ``qa_benchmark`` is supplied, a per-question isolation addendum is
            appended so each emitted item is a standalone mat_agent_bench task.
        source_type: Either ``"frame"`` (default — read the curated knowledge frame)
            or ``"markdown"`` (read the raw processed-markdown of the project's papers).
        source_id: The ``frame_id`` when ``source_type == "frame"``, otherwise the
            ``project_id`` to feed into ``get_project_markdown``.

    Returns:
        Complete prompt string for the projection agent.
    """
    schema_str = json.dumps(extraction_schema, indent=2)

    source_kind = (source_type or "frame").strip().lower()
    project_id_for_images = project_id or (source_id if source_kind == "markdown" else None) or ""
    vision_block = f"""\
## Inspecting figures and images

The processed Markdown may contain image references like
``![](images/<hash>.jpg)``.  Some critical information (protein
sequences, gel images, schema diagrams, plots, chemical structures)
appears **only inside these figures** and is invisible in the plain
text.  When the schema requires data that the surrounding text does
not contain but a nearby figure plausibly does, use the vision tools:

1. ``list_project_images(project_id="{project_id_for_images}")`` to see
   what images are available for this project.
2. ``read_image_with_ocr(project_id="{project_id_for_images}", image_ref=...)``
   for text-heavy images (sequences, tables-as-image, captions).
3. ``read_image_with_vision(project_id="{project_id_for_images}", image_ref=..., question=...)``
   for figures requiring interpretation (diagrams, plots, multi-panel
   composites).  Keep ``question`` narrowly scoped to the schema field
   you are filling.

Prefer OCR first for sequence-like or text-like images (faster, no LLM
cost).  Fall back to the vision call if OCR returns garbage or the
image is not text-based.  Do not call the vision tools on every image —
only on the ones that plausibly contain schema-relevant data.
"""
    if source_kind == "markdown":
        source_block = f"""\
The source for this projection is the **raw processed Markdown** of the
project's papers (the extraction step was skipped, so no curated knowledge
frame is consulted).

1. Plan your reads. Start with
   ``list_project_markdown_files(project_id="{source_id or ''}")`` to see
   every processed Markdown file with its total character length and
   heading count.
2. **Relevance check**: For each file (or just the first if the project is
   a single paper), read a small slice — either the first chunk of
   ``read_project_markdown_file(asset_id, start_char=0)`` or one or two
   headings via ``read_markdown_section(asset_id, section_heading=...)`` —
   and judge whether the subject matter is relevant to this space's domain
   ({domain}).
   - If clearly outside the domain, call
     ``mark_projection_not_relevant(projection_id, reason)`` and **stop —
     do not call ``save_projection``**.
3. **Walk the files section by section.** For every file in scope:
   - Use ``list_markdown_headings(asset_id)`` to see structure, then
     ``read_markdown_section(asset_id, section_heading=...)`` to read just
     the section you need. For files without useful headings, page through
     with ``read_project_markdown_file(asset_id, start_char=<end>)`` until
     no ``[TRUNCATED …]`` banner remains.
   - Do NOT just dump every file with ``get_project_markdown`` — that
     silently truncates and wastes context.
4. **Incremental writes** — after reading a section, extract just the
   items grounded in that section and append them with
   ``update_projection(projection_id, additions={{"<key>": [<items>]}})``.
   The first write should be ``save_projection`` with at least the
   top-level shape (it can be empty lists for list-typed keys); after that,
   use ``update_projection`` for every subsequent batch so you never
   re-emit the whole payload.
5. Do not invent facts that are not present in the Markdown. For required
   fields where data is genuinely absent in the source, set the value to
   null and note the gap in your assessment.
6. When every relevant section has been processed, you are done. The
   projection status is set to COMPLETED by the first ``save_projection``
   call; ``update_projection`` keeps it COMPLETED.

Notes:
- ``request_frame_clarification`` and ``get_frame_content`` are not
  available in this mode; do not call them.
- Use ``flag_for_feedback`` only for structural pipeline issues — see below.
"""
    else:
        source_block = """\
1. Call `get_frame_content` to read the knowledge frame content.
   - The response includes an `agent_annotations` field with two sub-keys:
     - `clarifications`: past clarification Q&A that has already been resolved.
     - `resolved_feedback`: past feedback items that have already been handled.
2. **Relevance check**: Before extracting anything, assess whether the paper's
   subject matter is relevant to this space's domain.
   - Review the frame's `content` summary (title, abstract, keywords if present).
   - If the paper clearly covers a completely different field (e.g. a
     physics / materials-physics topic applied against a biomedical space,
     or vice-versa), call `mark_projection_not_relevant(projection_id, reason)`
     with a concise explanation and **stop — do not call `save_projection`**.
   - If there is any plausible overlap, proceed with extraction.
3. Analyze the frame systematically, field by field.
4. Extract data matching each field in the schema.
5. **If a field is unclear, missing, or ambiguous**, first check `agent_annotations.clarifications` to see if the same question was answered in a previous run. If a matching entry exists, use that answer directly — do NOT call `request_frame_clarification` again.
   - Only call `request_frame_clarification` if the question is genuinely new (not in the annotations).
   - After it returns, call `get_frame_content` again to read the updated frame before continuing.
   - **Hard limit: call `request_frame_clarification` at most 3 times total.** After that, proceed with extraction using whatever data is available; set missing fields to null.
6. For required fields where data is genuinely absent in the source, set the value to null and note the gap in your assessment.
7. **Incremental writes**: call `save_projection` ONCE to establish the
   top-level shape (it can be empty lists for list-typed keys, plus any
   scalar fields you already have). For every additional batch of items
   you extract while exploring the frame, append them via
   `update_projection(projection_id, additions={"<key>": [<items>]})`
   instead of re-calling `save_projection`. This keeps each tool call
   small and avoids re-emitting the entire payload.
8. Use `flag_for_feedback` only if you encounter a **structural or architectural problem** with the extraction pipeline itself (see guidelines below). Before flagging, check `agent_annotations.resolved_feedback` — if the same issue was already resolved or dismissed, do NOT re-flag it.
"""

    prompt = f"""\
You are a structured data extraction agent. You are given source material from a research project and must extract specific structured data according to the schema below.

Domain: {domain}

{system_prompt}

---

# Required Output Schema

```json
{schema_str}
```

---

# Workflow

{source_block}

---

{vision_block}

---

# Guidelines

- **Relevance first**: If the paper's subject is entirely outside this space's domain, call `mark_projection_not_relevant` and stop. Do not call `save_projection` in that case.
- **Tool-use discipline**:
  - Prefer **sections over full files**: `list_markdown_headings` + `read_markdown_section` before `read_project_markdown_file`; never use `get_project_markdown` for a project with more than one or two short files.
  - For long single files, page through with `read_project_markdown_file(asset_id, start_char=<end>)` until no `[TRUNCATED …]` banner remains. Silent truncation = lost data downstream.
  - Prefer **incremental writes**: one `save_projection` to seed the shape, then `update_projection(additions={...})` per batch. Do not re-emit the full payload on every save.
  - Do not append the same item twice — track in your reasoning what you have already written.
  - Keep tool arguments small. Pass only the new items in `additions`.
- Extract ONLY from the source content — do not fabricate data.
- Preserve numerical precision — do not round values.
- Include units wherever applicable.
- If a field has multiple possible values and the schema says `type: list`, keep them as a JSON array internally.
- Use role fields carefully: mark controls, comparisons, and background references explicitly rather than treating them as primary extracted entities.
- For each row-like extracted record, include `is_core_study_data: true` when it represents the main material/data/function being investigated; set it to `false` for complementary, control, comparison, validation, or testing-only entries.
- If `evidence_level` is present, assign the highest supported level using this rubric: 1 = in vivo functional validation, 2 = in vitro direct mineralization experiment, 3 = indirect experimental evidence, 4 = prediction/hypothesis/inference.
- Note confidence level in your agent_notes for fields where the mapping is uncertain.

## When to use `request_frame_clarification` vs `flag_for_feedback` vs `mark_projection_not_relevant`

| Situation | Tool to use |
|-----------|-------------|
| Paper's subject is entirely outside the space domain | `mark_projection_not_relevant` |
| A specific value is missing from the frame but likely exists in the source | `request_frame_clarification` |
| An entry is ambiguous and needs detail from the source paper | `request_frame_clarification` |
| A numeric value lacks units or conditions | `request_frame_clarification` |
| The entire category of data is never extracted across many frames (pipeline gap) | `flag_for_feedback` |
| Evidence-level assignment is systematically wrong (prompt design issue) | `flag_for_feedback` |
| A data class is structurally absent from the extraction architecture | `flag_for_feedback` |

Prefer `request_frame_clarification` — it resolves the issue immediately. Only escalate to `flag_for_feedback` for recurring, architectural concerns that cannot be fixed by re-reading the source.

**Important:** Always check `agent_annotations` before calling either tool. If the same question or issue was already handled in a prior run, skip the tool call and use the recorded answer.
"""

    if (purpose or "").strip().lower() == "qa_benchmark":
        prompt += _QA_BENCHMARK_ADDENDUM

    return prompt
