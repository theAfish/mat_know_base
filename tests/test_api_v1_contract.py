from datetime import datetime, timezone

from mkb.web.api_server import app
from mkb.web.contracts_v1 import IntegrationEvent, ProvenanceReference
from mkb.web.routers.api_v1 import discover_api_v1


def test_v1_discovery_is_registered_without_replacing_legacy_routes():
    discovery = discover_api_v1()
    paths = app.openapi()["paths"]

    assert discovery.api_version == "v1"
    assert discovery.capabilities[0].status == "available"
    assert "get" in paths["/api/v1"]
    assert "get" in paths["/api/projects"]


def test_integration_event_serializes_stable_logical_references():
    event = IntegrationEvent(
        event_id="event-1",
        event_type="fact.revision.published",
        occurred_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        subject=ProvenanceReference(
            service="mkb",
            resource_type="fact_revision",
            resource_id="fact-1",
            revision="3",
        ),
        correlation_id="run-1",
        payload={"evidence_ids": ["evidence-1"]},
    )

    serialized = event.model_dump(mode="json")

    assert serialized["schema_version"] == "1"
    assert serialized["subject"]["revision"] == "3"
    assert serialized["payload"] == {"evidence_ids": ["evidence-1"]}