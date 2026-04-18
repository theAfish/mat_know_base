# TODOs

## 0. Refactor the codebase
- [x] Refactor the codebase to improve modularity and maintainability. Also for better implementation of the following todos.
  - Split `agents/tools.py` into `agents/tools/` package (reading.py, frames.py, projection.py, feedback.py)
  - Extracted generic `AgentRunner` from extraction.py into `agents/runner.py`
  - Moved prompts to `agents/prompts/` (kb_extraction.py, review.py, projection.py, feedback_review.py)
  - Slimmed down extraction.py to use new modules

## 1. Flexible knowledge base extraction
- [x] Do not use a fixed set of templates for knowledge base extraction, unless the keys are well-defined and consistent for all fields (such as paper metadata, domain). Let the agents decide how to record information in the knowledge base, and how to structure it. There is only one law: be specific and capture all statements and data mentioned in the research (excluding the data from references). This allows for more adaptability and scalability, as the agent can learn to extract relevant information based on the context and the task at hand.
  - Fixed keys: `paper` (metadata) and `domain` (string)
  - All other keys: free-form, agent-decided, each mapping to a list of dicts with `evidence_level`
  - Lightweight validation warns but doesn't block saves

## 2. Optimize multi-turn extraction
- [x] After the first round of extraction, the agent should be able to review the extracted knowledge base and identify any missing information or inconsistencies. The agent can then ask follow-up questions to fill in the gaps or clarify any ambiguities. This iterative process allows for a more comprehensive and accurate knowledge base, as the agent can refine its understanding of the research through multiple rounds of interaction.
  - Review agent (`agents/review.py`) with dedicated prompt
  - `ExtractionPass` DB model tracks each pass
  - `update_knowledge_frame` tool for incremental updates
  - `--max-passes N` CLI flag controls number of passes
  - Early stopping when review finds no changes needed

## 3. Downstream structured database construction
- [x] Different domain as different "space": `Space` DB model with extraction_schema, system_prompt, field_descriptions
- [x] "Data extraction as projections of the knowledge base": `Projection` DB model, projection agent reads frames and extracts per space schema
- [x] Finish the database construction pipeline and python interface: `spaces/registry.py` for CRUD, `agents/projection.py` for execution, API + CLI endpoints

## 4. Fancy visualization and user interface
- [x] Streamlit UI with 4 pages:
  - Projects: browse projects, view details, assets, extraction history
  - Knowledge Frames: view frames with expandable sections, evidence level coloring, graph visualization
  - Projections: select space, view projection data, space details
  - Feedback: dashboard with filters, inline resolution

## 5. Agentic feedback
- [x] `Feedback` DB model with categories (missing_data, ambiguous_data, inconsistency, etc.)
- [x] `FeedbackStatus` enum: OPEN, ACKNOWLEDGED, RESOLVED, DISMISSED, DEV_ISSUE
- [x] Projection agent can call `flag_for_feedback` during extraction
- [x] `feedback/manager.py` for CRUD operations
- [x] Feedback review agent (`agents/feedback_reviewer.py`) activated by user
- [x] CLI: `mkb feedback`, `mkb review-feedback`, `mkb resolve-feedback`

## 6. Dev agent (To be implemented later)
- [x] Interface designed in `agents/dev_agent.py`
- [ ] Actual implementation deferred
  - `DevAgentInterface` with `analyze_feedback`, `suggest_prompt_changes`, `suggest_space_changes`
  - `DevRecommendation`, `PromptPatch`, `SpacePatch` dataclasses defined
  - `DEV_ISSUE` status in FeedbackStatus enum ready for use
