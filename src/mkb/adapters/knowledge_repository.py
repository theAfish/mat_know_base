"""Portable SQLAlchemy repository for reviewed knowledge publication."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import func, insert, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from mkb.adapters.generic_repositories import (
    _json_value,
    _now,
    _Repository,
    _utc,
    collections_table,
    draft_revisions_table,
    drafts_table,
    fact_revisions_table,
    fact_sets_table,
    integration_outbox_table,
    review_decisions_table,
)
from mkb.exceptions import ConflictError, NotFoundError
from mkb.models import DraftGraph, DraftRevision, FactRevision, OutboxEvent, ReviewDecision


class GenericKnowledgeRepository(_Repository):
    def __init__(self, database, session=None, *, collection_exists: Callable | None = None):
        super().__init__(database, session)
        self._collection_exists = collection_exists

    @staticmethod
    def _draft(row) -> DraftGraph:
        values = row._mapping
        return DraftGraph(
            id=uuid.UUID(values["id"]),
            collection_id=uuid.UUID(values["collection_id"]),
            status=values["status"],
            current_revision=values["current_revision"],
            idempotency_key=values["idempotency_key"],
            target_fact_revision_id=(
                uuid.UUID(values["target_fact_revision_id"])
                if values.get("target_fact_revision_id")
                else None
            ),
            correction_context=(
                dict(values["correction_context"])
                if values.get("correction_context")
                else None
            ),
            created_by=values["created_by"],
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
        )

    @staticmethod
    def _revision(row) -> DraftRevision:
        values = row._mapping
        return DraftRevision(
            id=uuid.UUID(values["id"]),
            draft_id=uuid.UUID(values["draft_id"]),
            revision=values["revision"],
            graph=dict(values["graph"]),
            evidence_ids=tuple(uuid.UUID(value) for value in values["evidence_ids"]),
            author=values["author"],
            change_note=values["change_note"],
            idempotency_key=values["idempotency_key"],
            created_at=_utc(values["created_at"]),
        )

    @staticmethod
    def _decision(row) -> ReviewDecision:
        values = row._mapping
        return ReviewDecision(
            id=uuid.UUID(values["id"]),
            draft_id=uuid.UUID(values["draft_id"]),
            draft_revision=values["draft_revision"],
            decision=values["decision"],
            actor=values["actor"],
            notes=values["notes"],
            idempotency_key=values["idempotency_key"],
            created_at=_utc(values["created_at"]),
        )

    @staticmethod
    def _fact(row) -> FactRevision:
        values = row._mapping
        return FactRevision(
            id=uuid.UUID(values["id"]),
            fact_set_id=uuid.UUID(values["fact_set_id"]),
            revision=values["revision"],
            draft_id=uuid.UUID(values["draft_id"]),
            draft_revision=values["draft_revision"],
            data=dict(values["data"]),
            evidence_ids=tuple(uuid.UUID(value) for value in values["evidence_ids"]),
            status=values["status"],
            published_by=values["published_by"],
            created_at=_utc(values["created_at"]),
        )

    @staticmethod
    def _event(row) -> OutboxEvent:
        values = row._mapping
        return OutboxEvent(
            id=uuid.UUID(values["id"]),
            event_type=values["event_type"],
            subject_type=values["subject_type"],
            subject_id=uuid.UUID(values["subject_id"]),
            payload=dict(values["payload"]),
            correlation_id=values["correlation_id"],
            occurred_at=_utc(values["occurred_at"]),
            published_at=_utc(values["published_at"]),
        )

    def create_draft(self, **values):
        with self._session(write=True) as session:
            idempotency_key = values["idempotency_key"]
            if idempotency_key:
                existing = session.execute(
                    select(drafts_table).where(drafts_table.c.idempotency_key == idempotency_key)
                ).one_or_none()
                if existing:
                    draft = self._draft(existing)
                    if draft.target_fact_revision_id is not None:
                        raise ConflictError(
                            "Draft idempotency key belongs to another operation"
                        )
                    revision = self._get_revision(session, draft.id, draft.current_revision)
                    return draft, revision
            collection_id = str(values["collection_id"])
            exists = (
                self._collection_exists(session, collection_id)
                if self._collection_exists is not None
                else session.execute(
                    select(collections_table.c.id).where(collections_table.c.id == collection_id)
                ).scalar_one_or_none() is not None
            )
            if not exists:
                raise NotFoundError(f"Collection not found: {collection_id}")
            now = _now()
            draft_id = uuid.uuid4()
            revision_id = uuid.uuid4()
            session.execute(insert(drafts_table).values(
                id=str(draft_id), collection_id=collection_id, status="DRAFT",
                current_revision=1, idempotency_key=idempotency_key,
                created_by=values["actor"], created_at=now, updated_at=now,
            ))
            session.execute(insert(draft_revisions_table).values(
                id=str(revision_id), draft_id=str(draft_id), revision=1,
                graph=_json_value(values["graph"], "graph"),
                evidence_ids=[str(value) for value in values["evidence_ids"]],
                author=values["actor"], change_note="Initial draft",
                idempotency_key=None, created_at=now,
            ))
            return self._draft(session.execute(
                select(drafts_table).where(drafts_table.c.id == str(draft_id))
            ).one()), self._get_revision(session, draft_id, 1)

    def create_correction_request(self, **values):
        with self._session(write=True) as session:
            idempotency_key = values["idempotency_key"]
            if idempotency_key:
                existing = session.execute(
                    select(drafts_table).where(
                        drafts_table.c.idempotency_key == idempotency_key
                    )
                ).one_or_none()
                if existing:
                    draft = self._draft(existing)
                    if draft.target_fact_revision_id is None:
                        raise ConflictError(
                            "Correction idempotency key belongs to another operation"
                        )
                    if draft.target_fact_revision_id != values["target_fact_revision_id"]:
                        raise ConflictError(
                            "Correction idempotency key belongs to another fact revision"
                        )
                    return draft, self._get_revision(
                        session, draft.id, draft.current_revision
                    )
            target_id = str(values["target_fact_revision_id"])
            target = session.execute(
                select(fact_revisions_table).where(
                    fact_revisions_table.c.id == target_id
                )
            ).one_or_none()
            if target is None:
                raise NotFoundError(f"Fact revision not found: {target_id}")
            target_values = target._mapping
            latest_revision = session.execute(
                select(func.max(fact_revisions_table.c.revision)).where(
                    fact_revisions_table.c.fact_set_id
                    == target_values["fact_set_id"]
                )
            ).scalar_one()
            if target_values["revision"] != latest_revision:
                raise ConflictError("Correction target is not the latest fact revision")
            fact_set = session.execute(
                select(fact_sets_table).where(
                    fact_sets_table.c.id == target_values["fact_set_id"]
                )
            ).one()
            now = _now()
            draft_id = uuid.uuid4()
            revision_id = uuid.uuid4()
            session.execute(insert(drafts_table).values(
                id=str(draft_id),
                collection_id=fact_set._mapping["collection_id"],
                status="DRAFT",
                current_revision=1,
                idempotency_key=idempotency_key,
                target_fact_revision_id=target_id,
                correction_context=_json_value(
                    values["correction_context"], "correction_context"
                ),
                created_by=values["actor"],
                created_at=now,
                updated_at=now,
            ))
            session.execute(insert(draft_revisions_table).values(
                id=str(revision_id),
                draft_id=str(draft_id),
                revision=1,
                graph=target_values["data"],
                evidence_ids=target_values["evidence_ids"],
                author=values["actor"],
                change_note="Correction request imported from KDG",
                idempotency_key=None,
                created_at=now,
            ))
            draft = self._draft(session.execute(
                select(drafts_table).where(drafts_table.c.id == str(draft_id))
            ).one())
            return draft, self._get_revision(session, draft_id, 1)

    def get_draft(self, draft_id):
        with self._session() as session:
            row = session.execute(
                select(drafts_table).where(drafts_table.c.id == str(draft_id))
            ).one_or_none()
            return self._draft(row) if row else None

    def list_drafts(self, *, collection_id, status, limit, offset):
        statement = select(drafts_table).order_by(
            drafts_table.c.updated_at.desc(), drafts_table.c.id
        )
        if collection_id is not None:
            statement = statement.where(
                drafts_table.c.collection_id == str(collection_id)
            )
        if status is not None:
            statement = statement.where(drafts_table.c.status == status)
        statement = statement.limit(limit).offset(offset)
        with self._session() as session:
            return [self._draft(row) for row in session.execute(statement)]

    def _get_revision(self, session, draft_id, revision=None):
        statement = select(draft_revisions_table).where(
            draft_revisions_table.c.draft_id == str(draft_id)
        )
        if revision is not None:
            statement = statement.where(draft_revisions_table.c.revision == revision)
        else:
            statement = statement.order_by(draft_revisions_table.c.revision.desc()).limit(1)
        row = session.execute(statement).one_or_none()
        return self._revision(row) if row else None

    def get_revision(self, draft_id, revision=None):
        with self._session() as session:
            return self._get_revision(session, draft_id, revision)

    def revise(self, **values):
        with self._session(write=True) as session:
            idempotency_key = values["idempotency_key"]
            if idempotency_key:
                existing = session.execute(select(draft_revisions_table).where(
                    draft_revisions_table.c.idempotency_key == idempotency_key
                )).one_or_none()
                if existing:
                    return self._revision(existing)
            draft = session.execute(select(drafts_table).where(
                drafts_table.c.id == str(values["draft_id"])
            )).one_or_none()
            if not draft:
                raise NotFoundError(f"Draft not found: {values['draft_id']}")
            current = self._draft(draft)
            if current.status != "DRAFT":
                raise ConflictError(f"Draft cannot be revised while {current.status}")
            if current.current_revision != values["expected_revision"]:
                raise ConflictError(
                    f"Draft revision conflict: expected {values['expected_revision']}, "
                    f"current {current.current_revision}"
                )
            next_revision = current.current_revision + 1
            now = _now()
            revision_id = uuid.uuid4()
            try:
                session.execute(insert(draft_revisions_table).values(
                    id=str(revision_id), draft_id=str(current.id), revision=next_revision,
                    graph=_json_value(values["graph"], "graph"),
                    evidence_ids=[str(value) for value in values["evidence_ids"]],
                    author=values["actor"], change_note=values["change_note"],
                    idempotency_key=idempotency_key, created_at=now,
                ))
            except IntegrityError as exc:
                raise ConflictError("Draft revision idempotency key already exists") from exc
            session.execute(update(drafts_table).where(
                drafts_table.c.id == str(current.id),
                drafts_table.c.current_revision == current.current_revision,
            ).values(current_revision=next_revision, updated_at=now))
            return self._get_revision(session, current.id, next_revision)

    def submit_review(self, **values):
        return self._record_decision("SUBMITTED", "IN_REVIEW", "DRAFT", **values)

    def reject(self, **values):
        return self._record_decision("REJECTED", "REJECTED", "IN_REVIEW", **values)

    def _record_decision(self, decision, status, expected_status, **values):
        with self._session(write=True) as session:
            existing = self._existing_decision(session, values["idempotency_key"])
            if existing:
                if existing.decision != decision or existing.draft_id != values["draft_id"]:
                    raise ConflictError("Review idempotency key belongs to another operation")
                return existing
            draft = self._require_current_draft(session, values["draft_id"], values["expected_revision"])
            if draft.status != expected_status:
                raise ConflictError(f"Draft cannot be {decision.lower()} while {draft.status}")
            row = self._insert_decision(session, decision, values)
            session.execute(update(drafts_table).where(
                drafts_table.c.id == str(draft.id)
            ).values(status=status, updated_at=_now()))
            return row

    def approve(self, **values):
        with self._session(write=True) as session:
            existing = self._existing_decision(session, values["idempotency_key"])
            if existing:
                if existing.decision != "APPROVED" or existing.draft_id != values["draft_id"]:
                    raise ConflictError("Review idempotency key belongs to another operation")
                fact = session.execute(select(fact_revisions_table).where(
                    fact_revisions_table.c.draft_id == str(values["draft_id"]),
                    fact_revisions_table.c.draft_revision == existing.draft_revision,
                )).one()
                event = session.execute(select(integration_outbox_table).where(
                    integration_outbox_table.c.subject_id == fact._mapping["id"]
                )).one()
                return existing, self._fact(fact), self._event(event)
            draft = self._require_current_draft(session, values["draft_id"], values["expected_revision"])
            if draft.status != "IN_REVIEW":
                raise ConflictError(f"Draft cannot be approved while {draft.status}")
            revision = self._get_revision(session, draft.id, draft.current_revision)
            now = _now()
            target = None
            if draft.target_fact_revision_id is not None:
                target = session.execute(select(fact_revisions_table).where(
                    fact_revisions_table.c.id == str(draft.target_fact_revision_id)
                )).one_or_none()
                if target is None:
                    raise NotFoundError(
                        f"Fact revision not found: {draft.target_fact_revision_id}"
                    )
                latest_revision = session.execute(
                    select(func.max(fact_revisions_table.c.revision)).where(
                        fact_revisions_table.c.fact_set_id
                        == target._mapping["fact_set_id"]
                    )
                ).scalar_one()
                if target._mapping["revision"] != latest_revision:
                    raise ConflictError(
                        "Correction target is no longer the latest fact revision"
                    )
                fact_set_id = uuid.UUID(target._mapping["fact_set_id"])
                fact_revision = latest_revision + 1
            else:
                fact_set_id = uuid.uuid4()
                fact_revision = 1
            fact_id = uuid.uuid4()
            event_id = uuid.uuid4()
            if target is None:
                session.execute(insert(fact_sets_table).values(
                    id=str(fact_set_id), collection_id=str(draft.collection_id),
                    draft_id=str(draft.id), created_at=now,
                ))
            session.execute(insert(fact_revisions_table).values(
                id=str(fact_id), fact_set_id=str(fact_set_id), revision=fact_revision,
                draft_id=str(draft.id), draft_revision=draft.current_revision,
                data=revision.graph, evidence_ids=[str(value) for value in revision.evidence_ids],
                status="ACTIVE", published_by=values["actor"], created_at=now,
            ))
            if target is not None:
                session.execute(update(fact_revisions_table).where(
                    fact_revisions_table.c.id == str(draft.target_fact_revision_id)
                ).values(status="SUPERSEDED"))
            decision = self._insert_decision(session, "APPROVED", values)
            payload = {
                "fact_set_id": str(fact_set_id),
                "fact_revision_id": str(fact_id),
                "revision": fact_revision,
                "draft_id": str(draft.id),
                "draft_revision": draft.current_revision,
                "evidence_ids": [str(value) for value in revision.evidence_ids],
            }
            if draft.target_fact_revision_id is not None:
                payload["supersedes_fact_revision_id"] = str(
                    draft.target_fact_revision_id
                )
            session.execute(insert(integration_outbox_table).values(
                id=str(event_id), event_type="fact.revision.published",
                subject_type="fact_revision", subject_id=str(fact_id),
                payload=payload, correlation_id=values["correlation_id"],
                occurred_at=now, published_at=None,
            ))
            session.execute(update(drafts_table).where(
                drafts_table.c.id == str(draft.id)
            ).values(status="APPROVED", updated_at=now))
            fact = session.execute(select(fact_revisions_table).where(
                fact_revisions_table.c.id == str(fact_id)
            )).one()
            event = session.execute(select(integration_outbox_table).where(
                integration_outbox_table.c.id == str(event_id)
            )).one()
            return decision, self._fact(fact), self._event(event)

    def _require_current_draft(self, session, draft_id, expected_revision):
        row = session.execute(select(drafts_table).where(
            drafts_table.c.id == str(draft_id)
        )).one_or_none()
        if not row:
            raise NotFoundError(f"Draft not found: {draft_id}")
        draft = self._draft(row)
        if draft.current_revision != expected_revision:
            raise ConflictError(
                f"Draft revision conflict: expected {expected_revision}, current {draft.current_revision}"
            )
        return draft

    def _existing_decision(self, session, idempotency_key):
        if not idempotency_key:
            return None
        row = session.execute(select(review_decisions_table).where(
            review_decisions_table.c.idempotency_key == idempotency_key
        )).one_or_none()
        return self._decision(row) if row else None

    def _insert_decision(self, session, decision, values):
        now = _now()
        decision_id = uuid.uuid4()
        session.execute(insert(review_decisions_table).values(
            id=str(decision_id), draft_id=str(values["draft_id"]),
            draft_revision=values["expected_revision"], decision=decision,
            actor=values["actor"], notes=values["notes"],
            idempotency_key=values["idempotency_key"], created_at=now,
        ))
        return self._decision(session.execute(select(review_decisions_table).where(
            review_decisions_table.c.id == str(decision_id)
        )).one())

    def get_fact(self, fact_id):
        with self._session() as session:
            row = session.execute(select(fact_revisions_table).where(
                fact_revisions_table.c.id == str(fact_id)
            )).one_or_none()
            return self._fact(row) if row else None

    def list_facts(self, *, collection_id, limit, offset):
        statement = select(fact_revisions_table).join(fact_sets_table).order_by(
            fact_revisions_table.c.created_at.desc()
        ).limit(limit).offset(offset)
        if collection_id is not None:
            statement = statement.where(fact_sets_table.c.collection_id == str(collection_id))
        with self._session() as session:
            return [self._fact(row) for row in session.execute(statement)]

    def list_events(self, *, limit, offset):
        statement = select(integration_outbox_table).order_by(
            integration_outbox_table.c.occurred_at, integration_outbox_table.c.id
        ).limit(limit).offset(offset)
        with self._session() as session:
            return [self._event(row) for row in session.execute(statement)]


class KnowledgeSchemaManager:
    """Additively create only the reviewed-knowledge tables in legacy deployments."""

    def __init__(self, database):
        self._database = database

    def initialize(self) -> None:
        with self._database.transaction() as session:
            connection = session.connection()
            for table in (
                drafts_table,
                draft_revisions_table,
                review_decisions_table,
                fact_sets_table,
                fact_revisions_table,
                integration_outbox_table,
            ):
                table.create(connection, checkfirst=True)
            draft_columns = {
                column["name"]
                for column in sa_inspect(connection).get_columns("mkb_drafts")
            }
            if "target_fact_revision_id" not in draft_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE mkb_drafts "
                    "ADD COLUMN target_fact_revision_id VARCHAR(36)"
                )
            if "correction_context" not in draft_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE mkb_drafts ADD COLUMN correction_context JSON"
                )