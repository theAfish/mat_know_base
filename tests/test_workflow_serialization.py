import uuid
from types import SimpleNamespace

from mkb.services.workflows.serialization import serialize_raw_workflow


def test_serialize_raw_workflow_includes_dynamic_review_flags():
    eid = str(uuid.uuid4())
    row = SimpleNamespace(
        extraction_id=eid,
        project_id=uuid.uuid4(),
        version=2,
        schema_version="workflow-cards/2.0",
        extractor_version="workflow-extractor/2.0",
        model=None,
        status="COMPLETED",
        record_status="active",
        supersedes_extraction_id=None,
        correction_reason=None,
        correction_author=None,
        correction_details={},
        review_flags=[],
        provenance={},
        error=None,
        extracted_at=None,
        created_at=None,
        checkpoint=None,
        checkpoint_updated_at=None,
        graph={
            "schema_version": "workflow-cards/2.0",
            "paper_id": str(uuid.uuid4()),
            "extraction_id": eid,
            "nodes": [
                {
                    "node_id": f"raw:{eid}:n0001",
                    "raw_name": "input",
                    "node_kind": "object",
                    "node_kind_guess": "object",
                    "evidence_text": "input",
                    "paper_location": {},
                    "confidence": 0.9,
                },
                {
                    "node_id": f"raw:{eid}:n0002",
                    "raw_name": "calculate",
                    "node_kind": "operation",
                    "node_kind_guess": "operation",
                    "evidence_text": "calculate",
                    "paper_location": {},
                    "confidence": 0.9,
                },
            ],
            "edges": [],
        },
    )

    payload = serialize_raw_workflow(row, include_graph=False)

    assert any(flag["type"] == "missing_workflow_edges" for flag in payload["review_flags"])
