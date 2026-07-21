from mkb.materials import (
    MaterialFrames,
    MaterialProjections,
    MaterialWorkflows,
    Materials,
)


class _Operations:
    def __init__(self):
        self.calls = []

    def list_frames(self, **kwargs):
        self.calls.append(("list_frames", kwargs))
        return [{"project_id": "project-1"}]

    def project_all(self, **kwargs):
        self.calls.append(("project_all", kwargs))
        return {"status": "completed"}

    def review_raw_workflow(self, extraction_id, **kwargs):
        self.calls.append(("review_raw_workflow", extraction_id, kwargs))
        return {"status": "reviewed"}


def test_materials_namespaces_delegate_to_client_bound_operations():
    operations = _Operations()
    materials = Materials(
        frames=MaterialFrames(operations),
        projections=MaterialProjections(operations),
        workflows=MaterialWorkflows(operations=operations),
    )

    assert materials.frames.list(status="COMPLETED")[0]["project_id"] == "project-1"
    assert materials.projections.run_all(space_id="space-1") == {
        "status": "completed"
    }
    assert materials.workflows.review("workflow-1", author="reviewer") == {
        "status": "reviewed"
    }
    assert operations.calls == [
        ("list_frames", {"status": "COMPLETED"}),
        ("project_all", {"space_id": "space-1"}),
        ("review_raw_workflow", "workflow-1", {"author": "reviewer"}),
    ]
