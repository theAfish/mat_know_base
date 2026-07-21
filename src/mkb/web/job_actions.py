"""Registry for background job actions shared by web, agents, and legacy UI."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable


KwargsBuilder = Callable[..., dict[str, Any]]
Validator = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class JobAction:
    action: str
    kind: str
    label: str
    target: Callable[..., Any] | str
    build_kwargs: KwargsBuilder = lambda **kwargs: dict(kwargs)
    project_arg: str | None = "project_id"
    conflict_policy: str = "allow"
    validate: Validator | None = None


def _api_module():
    return importlib.import_module("mkb.api")


def _require_keys(*keys: str) -> Validator:
    def _validate(kwargs: dict[str, Any]) -> None:
        missing = [key for key in keys if kwargs.get(key) is None]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing required job action argument(s): {joined}")

    return _validate


def _projection_kwargs(**kwargs) -> dict[str, Any]:
    return {
        "space_id": kwargs["space_id"],
        "project_id": kwargs["project_id"],
        "source_type": kwargs.get("source_type", "frame"),
    }


def _feedback_review_all(progress_callback=None) -> dict[str, Any]:
    api = _api_module()
    projects = api.list_projects(limit=500)
    results = []
    for project in projects:
        project_id = project["project_id"]
        summary = api.get_feedback_summary(project_id)
        if int(summary.get("total", 0) or 0) == 0:
            continue
        if progress_callback:
            progress_callback({"message": f"Reviewing feedback for {project_id[:8]}"})
        results.append(api.review_feedback(project_id=project_id))
    return {"reviewed_projects": len(results), "results": results}


def _projection_review_kwargs(**kwargs) -> dict[str, Any]:
    return {
        "space_id": kwargs["space_id"],
        "project_id": kwargs["project_id"],
        "reviewer_id": kwargs.get("reviewer_id"),
    }


def _projection_review_all_kwargs(**kwargs) -> dict[str, Any]:
    return {
        "space_id": kwargs["space_id"],
        "project_ids": kwargs.get("project_ids"),
        "reviewer_id": kwargs.get("reviewer_id"),
    }


def _projection_review_session_kwargs(**kwargs) -> dict[str, Any]:
    return {
        "space_id": kwargs["space_id"],
        "project_ids": kwargs["project_ids"],
        "reviewer_id": kwargs.get("reviewer_id"),
    }


def _projection_review_followup_kwargs(**kwargs) -> dict[str, Any]:
    return {
        "space_id": kwargs["space_id"],
        "project_id": kwargs["project_id"],
        "message": kwargs["message"],
        "previous_job": kwargs["previous_job"],
        "reviewer_id": kwargs.get("reviewer_id"),
    }


def _graph_review_kwargs(**kwargs) -> dict[str, Any]:
    return {"mode": kwargs["mode"], "seed_count": kwargs["seed_count"]}


def _run_ontology_induction(**kwargs) -> dict[str, Any]:
    from mkb.agents.ontology_induction import run_ontology_induction

    return run_ontology_induction(**kwargs)


def _ontology_induction_kwargs(**kwargs) -> dict[str, Any]:
    return {
        "min_support": kwargs["min_support"],
        "author": kwargs["author"],
        "mode": kwargs["mode"],
        "sample_size": kwargs["sample_size"],
        "model": kwargs.get("model"),
        "verbose": kwargs.get("verbose", False),
    }


def _run_assistant_chat(
    *,
    runner,
    session_id: str,
    message: str,
    dispatch_pending_workflows: Callable[[], Any] | None = None,
    progress_callback=None,
):
    from mkb.agents.orchestrator import send_message

    result = send_message(
        runner=runner,
        session_id=session_id,
        message=message,
        progress_callback=progress_callback,
    )
    if dispatch_pending_workflows is not None:
        dispatch_pending_workflows()
    return result


def _assistant_chat_kwargs(**kwargs) -> dict[str, Any]:
    return {
        "runner": kwargs["runner"],
        "session_id": kwargs["session_id"],
        "message": kwargs["message"],
        "dispatch_pending_workflows": kwargs.get("dispatch_pending_workflows"),
    }


def _run_upload_ingest_action(*, payload, ingest_func, progress_callback=None):
    return ingest_func(payload, progress_callback=progress_callback)


def _upload_ingest_kwargs(**kwargs) -> dict[str, Any]:
    return {"payload": kwargs["payload"], "ingest_func": kwargs["ingest_func"]}


JOB_ACTIONS: dict[str, JobAction] = {
    "assistant_chat": JobAction(
        "assistant_chat",
        "orchestrator_chat",
        "Assistant",
        _run_assistant_chat,
        build_kwargs=_assistant_chat_kwargs,
        project_arg=None,
        conflict_policy="global_kind",
        validate=_require_keys("runner", "session_id", "message"),
    ),
    "upload_ingest": JobAction(
        "upload_ingest",
        "upload",
        "Upload Ingest",
        _run_upload_ingest_action,
        build_kwargs=_upload_ingest_kwargs,
        project_arg="job_project_id",
        conflict_policy="global_kind",
        validate=_require_keys("payload", "ingest_func"),
    ),
    "process_project": JobAction(
        "process_project",
        "process",
        "Process",
        "process",
        conflict_policy="project_kind",
        validate=_require_keys("project_id"),
    ),
    "extract_project": JobAction(
        "extract_project",
        "extract",
        "Extract",
        "extract",
        conflict_policy="project_kind",
        validate=_require_keys("project_id"),
    ),
    "project_to_space": JobAction(
        "project_to_space",
        "project",
        "Project",
        "project",
        build_kwargs=_projection_kwargs,
        conflict_policy="project_kind",
        validate=_require_keys("space_id", "project_id"),
    ),
    "extract_knowledge_graph": JobAction(
        "extract_knowledge_graph",
        "knowledge_graph",
        "Extract Graph",
        "extract_knowledge_graph",
        conflict_policy="project_kind",
        validate=_require_keys("project_id"),
    ),
    "extract_raw_workflow": JobAction(
        "extract_raw_workflow",
        "raw_workflow",
        "Extract Workflow",
        "extract_raw_workflow",
        conflict_policy="project_kind",
        validate=_require_keys("project_id"),
    ),
    "review_feedback": JobAction(
        "review_feedback",
        "feedback_review",
        "Review Feedback",
        "review_feedback",
        conflict_policy="project_kind",
        validate=_require_keys("project_id"),
    ),
    "review_feedback_all": JobAction(
        "review_feedback_all",
        "feedback_review",
        "Review Feedback",
        _feedback_review_all,
        build_kwargs=lambda **_kwargs: {},
        project_arg=None,
    ),
    "review_projection": JobAction(
        "review_projection",
        "projection_review",
        "Review Projection",
        "review_projections",
        build_kwargs=_projection_review_kwargs,
        conflict_policy="project_kind",
        validate=_require_keys("space_id", "project_id"),
    ),
    "review_projection_all": JobAction(
        "review_projection_all",
        "projection_review",
        "Review Projection",
        "review_projections_all",
        build_kwargs=_projection_review_all_kwargs,
        project_arg=None,
        validate=_require_keys("space_id"),
    ),
    "review_projection_session": JobAction(
        "review_projection_session",
        "projection_review",
        "Projection Review (session)",
        "review_projections_session",
        build_kwargs=_projection_review_session_kwargs,
        project_arg=None,
        conflict_policy="global_kind",
        validate=_require_keys("space_id", "project_ids"),
    ),
    "review_projection_followup": JobAction(
        "review_projection_followup",
        "projection_review",
        "Projection Review Follow-up",
        "review_projection_followup",
        build_kwargs=_projection_review_followup_kwargs,
        conflict_policy="project_kind",
        validate=_require_keys("space_id", "project_id", "message", "previous_job"),
    ),
    "review_graph": JobAction(
        "review_graph",
        "graph_review",
        "Graph Review",
        "review_knowledge_graph",
        build_kwargs=_graph_review_kwargs,
        project_arg=None,
        conflict_policy="global_kind",
        validate=_require_keys("mode", "seed_count"),
    ),
    "curate_workflow_schema": JobAction(
        "curate_workflow_schema",
        "ontology_induction",
        "Workflow Review Agent",
        _run_ontology_induction,
        build_kwargs=_ontology_induction_kwargs,
        project_arg=None,
        conflict_policy="global_kind",
        validate=_require_keys("min_support", "author", "mode", "sample_size"),
    ),
    "workflow_maintenance": JobAction(
        "workflow_maintenance",
        "workflow_maintenance",
        "Workflow Maintenance",
        "run_workflow_maintenance_task",
        project_arg=None,
        validate=_require_keys("task_id"),
    ),
}


WORKFLOW_KIND_ACTIONS = {
    "extraction": "extract_project",
    "projection": "project_to_space",
    "kg_extraction": "extract_knowledge_graph",
    "feedback_review": "review_feedback",
    "projection_review": "review_projection",
}


class JobActionConflict(RuntimeError):
    def __init__(self, action: JobAction, active_job: dict[str, Any]) -> None:
        self.action = action
        self.active_job = active_job
        status = str(active_job.get("status") or "active").lower()
        super().__init__(f"{action.label} is already {status}.")


def action_for_workflow_kind(kind: str) -> str:
    return WORKFLOW_KIND_ACTIONS[kind]


def job_action_start_params(
    action: str,
    *,
    job_project_id: str | None = None,
    label: str | None = None,
    services=None,
    **kwargs: Any,
) -> dict[str, Any]:
    spec = JOB_ACTIONS[action]
    if spec.validate is not None:
        spec.validate(kwargs)
    job_kwargs = spec.build_kwargs(**kwargs)
    project_id = job_project_id if spec.project_arg else None
    if isinstance(spec.target, str):
        target = (
            services.service(spec.target)
            if services is not None
            else getattr(_api_module(), spec.target)
        )
    else:
        target = spec.target
    return {
        "kind": spec.kind,
        "label": label or spec.label,
        "project_id": project_id,
        "target": target,
        "kwargs": job_kwargs,
    }


def _find_conflict(manager, spec: JobAction, project_id: str | None) -> dict[str, Any] | None:
    if spec.conflict_policy == "allow" or not hasattr(manager, "find_active_job"):
        return None
    if spec.conflict_policy == "project_kind":
        if not project_id:
            return None
        return manager.find_active_job(project_id=project_id, kind=spec.kind)
    if spec.conflict_policy == "global_kind":
        return manager.find_active_job(kind=spec.kind)
    raise ValueError(f"Unknown job action conflict policy: {spec.conflict_policy}")


def start_job_action(
    manager,
    action: str,
    *,
    job_project_id: str | None = None,
    label: str | None = None,
    idempotency_key: str | None = None,
    services=None,
    **kwargs: Any,
) -> str:
    spec = JOB_ACTIONS[action]
    params = job_action_start_params(
        action,
        job_project_id=job_project_id,
        label=label,
        services=services,
        **kwargs,
    )
    active = _find_conflict(manager, spec, params["project_id"])
    if active:
        raise JobActionConflict(spec, active)
    if spec.conflict_policy == "project_kind":
        params["active_key"] = f"project:{params['project_id']}:{spec.kind}"
    elif spec.conflict_policy == "global_kind":
        params["active_key"] = f"global:{spec.kind}"
    params["idempotency_key"] = idempotency_key
    try:
        return manager.start_job(**params)
    except Exception as exc:
        from mkb.jobs import JobConflict
        if isinstance(exc, JobConflict):
            raise JobActionConflict(spec, exc.existing) from exc
        raise
