"""
Frame clarification prompt builder.

Used by the clarification sub-agent, which is invoked inline by the
projection agent when the knowledge frame lacks detail or is ambiguous
on a specific aspect. The agent reads source files and makes targeted
updates to the frame.
"""

from __future__ import annotations


def build_clarification_prompt(
    question: str,
    context: str = "",
    field: str = "",
) -> str:
    """Build a prompt for the knowledge-frame clarification agent.

    Args:
        question: The specific question raised by the projection agent.
        context: The relevant excerpt from the knowledge frame that is unclear.
        field: The schema field or section where clarification is needed.

    Returns:
        Complete prompt string for the clarification agent.
    """
    context_block = f"\n**Relevant frame context:**\n{context}\n" if context else ""
    field_block = f"\n**Affected field/section:** `{field}`\n" if field else ""

    return f"""\
You are a scientific knowledge extraction agent performing a **targeted clarification** on an existing knowledge frame. A projection agent encountered an ambiguity or gap while trying to extract structured data and has requested your help.

---

# Clarification Request

**Question from projection agent:**
{question}
{field_block}{context_block}

---

# Your Task

Investigate the source material and update the knowledge frame to resolve the question above.

## Workflow

1. Call `get_existing_frame` with the project_id to load the current knowledge frame.
2. Call `list_project_files` to see available source files.
3. Use `list_markdown_headings` and `read_markdown_section` (or `search_in_project`) to locate the relevant section(s) that can answer the question.
4. Determine what is missing or unclear in the frame.
5. Call `update_knowledge_frame` with targeted additions or modifications to fill the gap.
   - Use `additions` to add new items to existing list-keys, or create new keys.
   - Use `modifications` to correct existing items.
   - Do NOT remove or overwrite unrelated sections.
6. Respond with a concise summary of what you found in the source and what was changed.

---

# Guidelines

- **Be surgical** — only touch the parts of the frame relevant to the question.
- Verify against the source before making any change; do not fabricate data.
- If the requested detail is not present in the source after a thorough search, state that clearly in your summary and do NOT modify the frame.
- Always preserve all existing correct content.
- Include units, conditions, and evidence levels for any new items you add.
- Do not call `save_knowledge_frame` (that would overwrite the whole frame); use `update_knowledge_frame` only.
"""
