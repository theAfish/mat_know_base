"""SDK-owned entry points for runtime-bound agent workflows."""

from __future__ import annotations

from mkb.agents.runtime import AgentRuntime


class AgentOperations:
    """Run materials agents with persistence explicitly owned by one client."""

    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime

    def extract(self, project_id=None, **kwargs) -> dict:
        from mkb.agents.extraction import run_extraction, run_extraction_all

        if project_id is not None:
            return run_extraction(project_id, runtime=self._runtime, **kwargs)
        return run_extraction_all(runtime=self._runtime, **kwargs)

    def review_feedback(self, project_id, **kwargs) -> dict:
        from mkb.agents.feedback_reviewer import run_feedback_review

        return run_feedback_review(project_id, runtime=self._runtime, **kwargs)
