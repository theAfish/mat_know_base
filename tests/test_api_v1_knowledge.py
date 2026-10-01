import httpx
import pytest
from fastapi import FastAPI

from mkb import KnowledgeBase
from mkb.config import Settings
from mkb.web.routers import api_v1
from mkb.web.security import ApiSecurityMiddleware

EDITOR_TOKEN = "editor-token-" + "e" * 40


@pytest.mark.asyncio
async def test_v1_draft_review_publication_flow(tmp_path, monkeypatch):
    knowledge_base = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'api-v1.db'}"
    )
    knowledge_base.initialize()
    collection = knowledge_base.collections.create(name="API review")
    monkeypatch.setattr(api_v1, "get_knowledge_base", lambda: knowledge_base)
    app = FastAPI()
    app.add_middleware(ApiSecurityMiddleware, settings=Settings(authentication_enabled=False))
    app.include_router(api_v1.router)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/v1/drafts?collection_id={collection.id}",
                headers={"Idempotency-Key": "draft-1"},
                json={"graph": {"claim": "supported"}, "evidence_ids": []},
            )
            draft_id = created.json()["draft"]["id"]
            drafts = await client.get(
                f"/api/v1/drafts?collection_id={collection.id}&status=DRAFT"
            )
            submitted = await client.post(
                f"/api/v1/drafts/{draft_id}/submit-review",
                headers={"Idempotency-Key": "submit-1"},
                json={"expected_revision": 1},
            )
            approved = await client.post(
                f"/api/v1/drafts/{draft_id}/approve",
                headers={"Idempotency-Key": "approve-1", "X-Request-ID": "oaw-run-1"},
                json={"expected_revision": 1, "notes": "verified"},
            )
            facts = await client.get(f"/api/v1/facts?collection_id={collection.id}")
            fact = await client.get(f"/api/v1/facts/{approved.json()['fact']['id']}")
            missing_fact = await client.get("/api/v1/facts/00000000-0000-0000-0000-000000000000")
            events = await client.get("/api/v1/integration-events")

        assert created.status_code == 201
        assert drafts.status_code == 200
        assert [item["id"] for item in drafts.json()] == [draft_id]
        assert submitted.status_code == 200
        assert approved.status_code == 200
        assert approved.json()["fact"]["data"] == {"claim": "supported"}
        assert facts.json()[0]["id"] == approved.json()["fact"]["id"]
        assert fact.status_code == 200
        assert fact.json() == approved.json()["fact"]
        assert missing_fact.status_code == 404
        assert events.json()[0]["correlation_id"] == "oaw-run-1"
    finally:
        knowledge_base.close()


@pytest.mark.asyncio
async def test_v1_approval_requires_publish_permission():
    app = FastAPI()
    app.add_middleware(
        ApiSecurityMiddleware,
        settings=Settings(
            authentication_enabled=True,
            auth_tokens={EDITOR_TOKEN: "editor"},
        ),
    )
    app.include_router(api_v1.router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/drafts/unknown/approve",
            headers={"Authorization": f"Bearer {EDITOR_TOKEN}"},
            json={"expected_revision": 1},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["required"] == "publish"


@pytest.mark.asyncio
async def test_v1_ingests_kdg_correction_as_linked_editable_draft(tmp_path, monkeypatch):
    knowledge_base = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'correction-intake.db'}"
    )
    knowledge_base.initialize()
    collection = knowledge_base.collections.create(name="Correction intake")
    source_draft, _ = knowledge_base.knowledge.create_draft(
        collection.id,
        {"band_gap_ev": 1.1, "method": "PBE"},
        actor="oaw:agent-1",
    )
    knowledge_base.knowledge.submit_review(
        source_draft.id, expected_revision=1, actor="oaw:agent-1"
    )
    _, fact, _ = knowledge_base.knowledge.approve(
        source_draft.id, expected_revision=1, actor="oaw:reviewer-1"
    )
    monkeypatch.setattr(api_v1, "get_knowledge_base", lambda: knowledge_base)
    app = FastAPI()
    app.add_middleware(ApiSecurityMiddleware, settings=Settings(authentication_enabled=False))
    app.include_router(api_v1.router)
    payload = {
        "source_event_id": "kdg-event-1",
        "source_correction_id": "correction-1",
        "target": {
            "service": "mkb",
            "resource_type": "fact_revision",
            "resource_id": str(fact.id),
            "revision": "1",
        },
        "issue_summary": "The functional was recorded incorrectly.",
        "proposed_correction": "Use the reviewed HSE06 result.",
        "evidence": [
            {
                "service": "kdg",
                "resource_type": "experience",
                "resource_id": "experience-1",
                "revision": "3",
            }
        ],
    }

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/correction-requests",
                headers={"X-Request-ID": "run-1", "X-Causation-ID": "kdg-event-1"},
                json=payload,
            )
            replayed = await client.post("/api/v1/correction-requests", json=payload)

        assert created.status_code == 201
        body = created.json()
        assert replayed.json()["draft"]["id"] == body["draft"]["id"]
        assert body["draft"]["status"] == "DRAFT"
        assert body["draft"]["target_fact_revision_id"] == str(fact.id)
        assert body["draft"]["correction_context"]["source_correction_id"] == "correction-1"
        assert body["revision"]["graph"] == fact.data
    finally:
        knowledge_base.close()