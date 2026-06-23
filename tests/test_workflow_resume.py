import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from mkb import api
from mkb.agents.tools import workflow_canonicalization as canonical_tools
from mkb.agents.tools import workflows as workflow_tools
from mkb.agents.workflow_canonicalization import canonicalization_call_budget
from mkb.web.routers import projects as projects_router


def test_extract_raw_workflow_returns_preflight_error_when_no_markdown(monkeypatch):
    monkeypatch.setattr(
        api,
        "get_raw_workflow_extraction_readiness",
        lambda _project_id: {"ready": False, "message": "No processed markdown"},
    )

    result = api.extract_raw_workflow(uuid.uuid4())

    assert result == {"status": "error", "message": "No processed markdown"}


def test_project_workflow_extract_rejects_active_job(monkeypatch):
    project_id = str(uuid.uuid4())

    monkeypatch.setattr(
        projects_router.jobs,
        "find_active_job",
        lambda **_kwargs: {"job_id": "j1", "status": "RUNNING"},
    )

    with pytest.raises(HTTPException) as exc:
        projects_router.project_workflow_extract(project_id)

    assert exc.value.status_code == 409
    assert "already running" in exc.value.detail.lower()


def test_project_workflow_extract_rejects_when_not_ready(monkeypatch):
    project_id = str(uuid.uuid4())

    monkeypatch.setattr(projects_router.jobs, "find_active_job", lambda **_kwargs: None)
    monkeypatch.setattr(
        projects_router.api,
        "get_raw_workflow_extraction_readiness",
        lambda _project_id: {"ready": False, "message": "Run Process first"},
    )

    with pytest.raises(HTTPException) as exc:
        projects_router.project_workflow_extract(project_id)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Run Process first"


def test_delete_raw_workflow_version_rejects_active_job(monkeypatch):
    project_id = str(uuid.uuid4())

    monkeypatch.setattr(
        projects_router.jobs,
        "find_active_job",
        lambda **_kwargs: {"job_id": "j1", "status": "RUNNING"},
    )

    with pytest.raises(HTTPException) as exc:
        projects_router.delete_project_workflow_version(project_id, 3)

    assert exc.value.status_code == 409
    assert "currently running" in exc.value.detail.lower()


def test_delete_raw_workflow_version_removes_unfinished_row(monkeypatch):
    project_id = uuid.uuid4()
    extraction_id = uuid.uuid4()
    fake_row = SimpleNamespace(
        extraction_id=extraction_id,
        project_id=project_id,
        version=4,
        status="FAILED",
        graph=None,
    )

    fake_query = MagicMock()
    fake_query.filter.return_value.first.return_value = fake_row
    fake_session = MagicMock()
    fake_session.query.return_value = fake_query
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_session
    fake_cm.__exit__.return_value = False

    monkeypatch.setattr(api, "init_db", lambda: None)
    monkeypatch.setattr(api, "SyncSessionLocal", lambda: fake_cm)

    result = api.delete_raw_workflow_version(project_id, 4)

    assert result == {
        "status": "deleted",
        "project_id": str(project_id),
        "version": 4,
        "extraction_id": str(extraction_id),
    }
    fake_session.delete.assert_called_once_with(fake_row)
    fake_session.commit.assert_called_once()


def test_delete_raw_workflow_version_rejects_completed_rows(monkeypatch):
    project_id = uuid.uuid4()
    fake_row = SimpleNamespace(
        extraction_id=uuid.uuid4(),
        project_id=project_id,
        version=2,
        status="COMPLETED",
        graph={"nodes": [], "edges": []},
    )

    fake_query = MagicMock()
    fake_query.filter.return_value.first.return_value = fake_row
    fake_session = MagicMock()
    fake_session.query.return_value = fake_query
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_session
    fake_cm.__exit__.return_value = False

    monkeypatch.setattr(api, "init_db", lambda: None)
    monkeypatch.setattr(api, "SyncSessionLocal", lambda: fake_cm)

    result = api.delete_raw_workflow_version(project_id, 2)

    assert result == {"error": "Completed raw workflow versions cannot be deleted"}
    fake_session.delete.assert_not_called()
    fake_session.commit.assert_not_called()


def test_checkpoint_raw_workflow_updates_unfinished_row(monkeypatch):
    extraction_id = uuid.uuid4()
    fake_row = SimpleNamespace(
        extraction_id=extraction_id,
        project_id=uuid.uuid4(),
        version=3,
        status="IN_PROGRESS",
        graph=None,
        checkpoint=None,
        checkpoint_updated_at=None,
        provenance={},
    )

    fake_session = MagicMock()
    fake_session.query.return_value.filter_by.return_value.first.return_value = fake_row
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_session
    fake_cm.__exit__.return_value = False

    monkeypatch.setattr(workflow_tools, "SyncSessionLocal", lambda: fake_cm)

    result = workflow_tools.checkpoint_raw_workflow(
        str(extraction_id),
        "Read methods section; synthesis branch drafted, characterization remains.",
        graph={"nodes": [{"node_id": "draft-1"}], "edges": []},
    )

    assert result["status"] == "checkpointed"
    assert result["checkpoint_count"] == 1
    assert fake_row.checkpoint["summary"].startswith("Read methods section")
    assert fake_row.checkpoint["graph"] == {"nodes": [{"node_id": "draft-1"}], "edges": []}
    assert fake_row.provenance["checkpoint_count"] == 1
    fake_session.commit.assert_called_once()


def test_checkpoint_canonical_workflow_updates_unfinished_row(monkeypatch):
    canonicalization_id = uuid.uuid4()
    raw_extraction_id = uuid.uuid4()
    fake_row = SimpleNamespace(
        canonicalization_id=canonicalization_id,
        project_id=uuid.uuid4(),
        raw_extraction_id=raw_extraction_id,
        version=2,
        status="IN_PROGRESS",
        graph=None,
        checkpoint=None,
        checkpoint_updated_at=None,
        provenance={},
    )
    fake_raw = SimpleNamespace(extraction_id=raw_extraction_id, graph={"nodes": [], "edges": []})

    fake_query = MagicMock()
    fake_query.filter_by.return_value.first.side_effect = [fake_row, fake_raw]
    fake_session = MagicMock()
    fake_session.query.return_value = fake_query
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_session
    fake_cm.__exit__.return_value = False

    monkeypatch.setattr(canonical_tools, "SyncSessionLocal", lambda: fake_cm)

    result = canonical_tools.checkpoint_canonical_workflow(
        str(canonicalization_id),
        "Mapped data objects and one operation; edge drafting remains.",
    )

    assert result["status"] == "checkpointed"
    assert result["checkpoint_count"] == 1
    assert fake_row.checkpoint["summary"].startswith("Mapped data objects")
    assert fake_row.checkpoint["graph"]["nodes"] == []
    assert fake_row.provenance["checkpoint_count"] == 1
    fake_session.commit.assert_called_once()


def test_upsert_canonical_node_persists_draft_graph(monkeypatch):
    canonicalization_id = uuid.uuid4()
    raw_extraction_id = uuid.uuid4()
    project_id = uuid.uuid4()
    fake_row = SimpleNamespace(
        canonicalization_id=canonicalization_id,
        project_id=project_id,
        raw_extraction_id=raw_extraction_id,
        version=2,
        schema_version="workflow-schema/1.0",
        status="IN_PROGRESS",
        graph=None,
        checkpoint={"summary": "seed", "graph": None},
        checkpoint_updated_at=None,
        provenance={},
    )
    fake_raw = SimpleNamespace(extraction_id=raw_extraction_id, graph={"nodes": [], "edges": []})

    fake_query = MagicMock()
    fake_query.filter_by.return_value.first.side_effect = [fake_row, fake_raw]
    fake_session = MagicMock()
    fake_session.query.return_value = fake_query
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_session
    fake_cm.__exit__.return_value = False

    monkeypatch.setattr(canonical_tools, "SyncSessionLocal", lambda: fake_cm)

    result = canonical_tools.upsert_canonical_node(
        str(canonicalization_id),
        {
            "node_id": f"canonical:{canonicalization_id}:n0001",
            "label": "DFT training frames",
            "node_kind": "object",
            "object_schema": "DataObject",
            "attributes": {"count": 1570, "splits": {"relaxation": 820, "aimd": 750}},
            "raw_node_ids": [f"raw:{raw_extraction_id}:n0001"],
        },
    )

    assert result["status"] == "checkpointed"
    assert result["action"] == "added"
    assert fake_row.checkpoint["graph"]["nodes"][0]["label"] == "DFT training frames"
    assert fake_row.checkpoint["graph"]["paper_id"] == str(project_id)
    fake_session.commit.assert_called_once()


def test_canonicalization_call_budget_scales_and_is_capped():
    assert canonicalization_call_budget(0, 0) == 80
    assert canonicalization_call_budget(20, 30) == 130
    assert canonicalization_call_budget(1000, 1000) == 240


def test_bulk_canonical_draft_tool_is_registered():
    assert canonical_tools.upsert_canonical_draft_batch in canonical_tools.CANONICALIZATION_TOOLS


def test_resume_manifest_omits_heavy_draft_attributes_and_tracks_remaining_raw_nodes():
    raw_ids = [f"raw:x:n{i:04d}" for i in range(1, 4)]
    raw_graph = {
        "nodes": [
            {"node_id": raw_id, "raw_name": f"raw {index}", "node_kind_guess": "object"}
            for index, raw_id in enumerate(raw_ids, 1)
        ]
    }
    draft = {
        "nodes": [{
            "node_id": "canonical:x:n0001", "label": "Material",
            "node_kind": "object", "raw_node_ids": [raw_ids[0]],
            "attributes": {"large": "x" * 10_000},
        }],
        "edges": [],
        "raw_to_canonical_mappings": [{
            "raw_node_ids": [raw_ids[0]],
            "canonical_node_ids": ["canonical:x:n0001"],
        }],
        "unmatched_raw_information": [],
    }

    manifest = canonical_tools._compact_draft_manifest(draft, raw_graph)

    assert manifest["counts"]["remaining_raw_nodes"] == 2
    assert [item["node_id"] for item in manifest["remaining_raw_nodes"]] == raw_ids[1:]
    assert len(json.dumps(manifest)) < 2_000


def test_global_recanonicalization_batch_runs_pending_tasks(monkeypatch):
    task_ids = [uuid.uuid4(), uuid.uuid4()]
    fake_query = MagicMock()
    fake_query.filter_by.return_value.order_by.return_value.all.return_value = [
        SimpleNamespace(
            task_id=task_id, project_id=uuid.uuid4(), status="pending", result={},
        ) for task_id in task_ids
    ]
    fake_session = MagicMock()
    fake_session.query.return_value = fake_query
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_session
    fake_cm.__exit__.return_value = False
    monkeypatch.setattr(api, "init_db", lambda: None)
    monkeypatch.setattr(api, "SyncSessionLocal", lambda: fake_cm)
    monkeypatch.setattr(
        api, "run_workflow_maintenance_task",
        lambda task_id, **_kwargs: {"task_id": str(task_id), "status": "completed"},
    )

    result = api.run_pending_recanonicalizations()

    assert result["task_count"] == 2
    assert result["completed"] == 2
    assert result["failed"] == 0


def test_global_recanonicalization_batch_coalesces_duplicate_project_tasks(monkeypatch):
    project_id = uuid.uuid4()
    newest = SimpleNamespace(
        task_id=uuid.uuid4(), project_id=project_id, status="pending", result={},
    )
    older = SimpleNamespace(
        task_id=uuid.uuid4(), project_id=project_id, status="pending", result={},
    )
    fake_query = MagicMock()
    fake_query.filter_by.return_value.order_by.return_value.all.return_value = [newest, older]
    fake_session = MagicMock()
    fake_session.query.return_value = fake_query
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_session
    fake_cm.__exit__.return_value = False
    monkeypatch.setattr(api, "init_db", lambda: None)
    monkeypatch.setattr(api, "SyncSessionLocal", lambda: fake_cm)
    monkeypatch.setattr(
        api, "run_workflow_maintenance_task",
        lambda task_id, **_kwargs: {"task_id": str(task_id), "status": "completed"},
    )

    result = api.run_pending_recanonicalizations()

    assert result["task_count"] == 1
    assert result["duplicate_tasks_coalesced"] == 1
    assert older.status == "superseded"
    assert older.result["superseded_by_task_id"] == str(newest.task_id)
