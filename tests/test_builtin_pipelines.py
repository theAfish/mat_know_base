from types import SimpleNamespace

from mkb import KnowledgeBase
from mkb.builtin_pipelines import register_materials_builtin_pipelines


def test_materials_builtin_pipelines_delegate_without_global_registration():
    calls = []

    def operation(name, *, progress=False):
        def invoke(**kwargs):
            callback = kwargs.pop("progress_callback", None)
            calls.append((name, kwargs))
            if progress and callback is not None:
                callback({"message": f"{name} progress", "stage": "working"})
            return {"operation": name, "arguments": kwargs}

        return invoke

    services = SimpleNamespace(
        ingest=operation("ingest"),
        process=operation("process", progress=True),
        extract=operation("extract", progress=True),
        project=operation("project", progress=True),
        extract_knowledge_graph=operation("extract_knowledge_graph", progress=True),
        extract_raw_workflow=operation("extract_raw_workflow", progress=True),
        review_schema_proposal=operation("review_schema_proposal"),
        review_feedback=operation("review_feedback", progress=True),
    )
    kb = KnowledgeBase(services=services)
    definitions = register_materials_builtin_pipelines(kb)

    assert [item.name for item in definitions] == [
        "materials.ingest",
        "materials.process",
        "materials.extract_frames",
        "materials.project",
        "materials.extract_graph",
        "materials.extract_workflow",
        "materials.review_schema",
        "materials.review_feedback",
    ]
    events = []
    run = kb.pipelines.run(
        "materials.extract_graph",
        inputs={"project_id": "project-1"},
        parameters={"model": "provider/model"},
        progress=events.append,
    )

    assert calls == [
        (
            "extract_knowledge_graph",
            {"project_id": "project-1", "model": "provider/model"},
        )
    ]
    assert run.outputs["result"] == {
        "operation": "extract_knowledge_graph",
        "arguments": {"project_id": "project-1", "model": "provider/model"},
    }
    progress = next(event for event in events if event.event == "step_progress")
    assert progress.message == "extract_knowledge_graph progress"
    assert progress.payload == {"stage": "working"}
