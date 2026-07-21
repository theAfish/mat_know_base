"""Application-owned SDK dependencies shared by FastAPI routes."""

from __future__ import annotations

from functools import lru_cache

from mkb import AssistantService, Jobs, KnowledgeBase


@lru_cache(maxsize=1)
def get_knowledge_base() -> KnowledgeBase:
    """Return the lazily configured application client for this server process."""
    from mkb.agents.orchestrator import create_orchestrator_runner
    from mkb.agents.tools.orchestrator_tools import get_pending_workflows
    from mkb.web._helpers import start_web_job_action
    from mkb.web._state import jobs
    from mkb.web.job_actions import action_for_workflow_kind
    from mkb.web.job_backend import WebJobBackend

    knowledge_base = KnowledgeBase.from_environment()
    backend = WebJobBackend(jobs)
    knowledge_base.job_backend = backend
    knowledge_base.jobs = Jobs(
        backend,
        action_submitter=lambda action, **kwargs: start_web_job_action(
            jobs, action, **kwargs
        ),
    )
    knowledge_base.pipelines._bind_job_backend(backend)
    knowledge_base.assistant = AssistantService(
        knowledge_base,
        session_factory=create_orchestrator_runner,
        pending_workflows=get_pending_workflows,
        action_resolver=action_for_workflow_kind,
    )
    return knowledge_base


def close_knowledge_base() -> None:
    """Release the application client during server shutdown."""
    if get_knowledge_base.cache_info().currsize:
        get_knowledge_base().close()
        get_knowledge_base.cache_clear()
