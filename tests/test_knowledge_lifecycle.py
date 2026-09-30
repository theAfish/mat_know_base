import uuid

import pytest

from mkb import ConflictError, KnowledgeBase


def _client(tmp_path):
    return KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'knowledge.db'}")


def test_draft_revisions_use_optimistic_concurrency_and_idempotency(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Reviewed facts")
        draft, initial = kb.knowledge.create_draft(
            collection.id,
            {"nodes": [{"id": "sample-1"}], "edges": []},
            actor="oaw:user-1",
            idempotency_key="create-1",
        )
        duplicate, duplicate_revision = kb.knowledge.create_draft(
            collection.id,
            {"ignored": True},
            actor="oaw:user-1",
            idempotency_key="create-1",
        )

        assert duplicate.id == draft.id
        assert duplicate_revision == initial
        revised = kb.knowledge.revise(
            draft.id,
            expected_revision=1,
            graph={"nodes": [{"id": "sample-1", "name": "Calcite"}], "edges": []},
            evidence_ids=[uuid.uuid4()],
            actor="oaw:user-1",
            change_note="Add canonical name",
            idempotency_key="revise-1",
        )
        assert revised.revision == 2
        assert kb.knowledge.get_draft(draft.id).current_revision == 2
        assert kb.knowledge.list_drafts(collection_id=collection.id) == [
            kb.knowledge.get_draft(draft.id)
        ]
        assert kb.knowledge.list_drafts(status="draft") == [
            kb.knowledge.get_draft(draft.id)
        ]
        assert kb.knowledge.list_drafts(status="in_review") == []
        with pytest.raises(ConflictError, match="expected 1, current 2"):
            kb.knowledge.revise(
                draft.id,
                expected_revision=1,
                graph={},
                actor="oaw:user-1",
            )


def test_approval_publishes_immutable_fact_and_outbox_atomically(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Publication")
        evidence_id = uuid.uuid4()
        draft, _ = kb.knowledge.create_draft(
            collection.id,
            {"claim": "Calcite is stable at ambient conditions"},
            evidence_ids=[evidence_id],
            actor="oaw:agent-1",
        )
        submitted = kb.knowledge.submit_review(
            draft.id,
            expected_revision=1,
            actor="oaw:agent-1",
            idempotency_key="submit-1",
        )
        with pytest.raises(ConflictError, match="another operation"):
            kb.knowledge.approve(
                draft.id,
                expected_revision=1,
                actor="oaw:reviewer-1",
                idempotency_key="submit-1",
            )
        decision, fact, event = kb.knowledge.approve(
            draft.id,
            expected_revision=1,
            actor="oaw:reviewer-1",
            notes="Evidence verified",
            idempotency_key="approve-1",
            correlation_id="oaw-run-1",
        )

        assert submitted.decision == "SUBMITTED"
        assert decision.decision == "APPROVED"
        assert fact.data["claim"].startswith("Calcite")
        assert fact.evidence_ids == (evidence_id,)
        assert event.event_type == "fact.revision.published"
        assert event.subject_id == fact.id
        assert event.correlation_id == "oaw-run-1"
        assert kb.knowledge.get_draft(draft.id).status == "APPROVED"
        assert kb.knowledge.list_facts(collection_id=collection.id) == [fact]
        assert kb.knowledge.list_events() == [event]


def test_outer_transaction_rolls_back_fact_and_outbox_together(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Rollback")
        draft, _ = kb.knowledge.create_draft(
            collection.id, {"claim": "temporary"}, actor="oaw:agent-1"
        )
        kb.knowledge.submit_review(
            draft.id, expected_revision=1, actor="oaw:agent-1"
        )

        with pytest.raises(RuntimeError, match="abort publication"):
            with kb.transaction() as transaction:
                transaction.knowledge.approve(
                    draft.id, expected_revision=1, actor="oaw:reviewer-1"
                )
                raise RuntimeError("abort publication")

        assert kb.knowledge.list_facts() == []
        assert kb.knowledge.list_events() == []
        assert kb.knowledge.get_draft(draft.id).status == "IN_REVIEW"


def test_rejection_closes_review_without_publishing(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Rejected draft")
        draft, _ = kb.knowledge.create_draft(
            collection.id, {"claim": "unsupported"}, actor="oaw:agent-1"
        )
        kb.knowledge.submit_review(draft.id, expected_revision=1, actor="oaw:agent-1")

        decision = kb.knowledge.reject(
            draft.id,
            expected_revision=1,
            actor="oaw:reviewer-1",
            notes="Evidence does not support the claim",
            idempotency_key="reject-1",
        )

        assert decision.decision == "REJECTED"
        assert kb.knowledge.get_draft(draft.id).status == "REJECTED"
        assert kb.knowledge.list_facts() == []
        assert kb.knowledge.list_events() == []
        with pytest.raises(ConflictError, match="cannot be approved while REJECTED"):
            kb.knowledge.approve(draft.id, expected_revision=1, actor="oaw:reviewer-1")


def test_correction_approval_appends_fact_revision_to_existing_fact_set(tmp_path):
    with _client(tmp_path) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Corrected facts")
        draft, _ = kb.knowledge.create_draft(
            collection.id,
            {"band_gap_ev": 1.1, "method": "PBE"},
            actor="oaw:agent-1",
        )
        kb.knowledge.submit_review(draft.id, expected_revision=1, actor="oaw:agent-1")
        _, original, _ = kb.knowledge.approve(
            draft.id, expected_revision=1, actor="oaw:reviewer-1"
        )

        correction, imported = kb.knowledge.create_correction_request(
            original.id,
            {
                "source_service": "kdg",
                "source_correction_id": "correction-1",
                "issue_summary": "The functional was recorded incorrectly.",
                "proposed_correction": "Use the reviewed HSE06 result.",
                "evidence": [
                    {
                        "service": "kdg",
                        "resource_type": "experience",
                        "resource_id": "experience-1",
                    }
                ],
            },
            actor="oaw:integration",
            idempotency_key="kdg-correction-1",
        )
        replayed, _ = kb.knowledge.create_correction_request(
            original.id,
            {"ignored": True},
            actor="oaw:integration",
            idempotency_key="kdg-correction-1",
        )

        assert replayed.id == correction.id
        assert imported.graph == original.data
        assert correction.target_fact_revision_id == original.id
        with pytest.raises(ConflictError, match="another operation"):
            kb.knowledge.create_draft(
                collection.id,
                {"ignored": True},
                actor="oaw:integration",
                idempotency_key="kdg-correction-1",
            )
        revised = kb.knowledge.revise(
            correction.id,
            expected_revision=1,
            graph={"band_gap_ev": 1.9, "method": "HSE06"},
            actor="oaw:reviewer-1",
        )
        kb.knowledge.submit_review(
            correction.id,
            expected_revision=revised.revision,
            actor="oaw:reviewer-1",
        )
        _, corrected, event = kb.knowledge.approve(
            correction.id,
            expected_revision=revised.revision,
            actor="oaw:reviewer-1",
        )

        facts = kb.knowledge.list_facts(collection_id=collection.id)
        original_after = next(item for item in facts if item.id == original.id)
        assert corrected.fact_set_id == original.fact_set_id
        assert corrected.revision == 2
        assert corrected.data == {"band_gap_ev": 1.9, "method": "HSE06"}
        assert original_after.status == "SUPERSEDED"
        assert event.payload["supersedes_fact_revision_id"] == str(original.id)