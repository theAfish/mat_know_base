"""SQLAlchemy ORM models."""

import enum
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Enum, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ── Enums ───────────────────────────────────────────────────────


class ProcessingStatus(str, enum.Enum):
    PENDING = "PENDING"
    STORED = "STORED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class ProcessingType(str, enum.Enum):
    MARKDOWN = "MARKDOWN"
    DATAFRAME = "DATAFRAME"
    IMAGE = "IMAGE"
    UNPROCESSABLE = "UNPROCESSABLE"


class FrameStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EvidenceLevel(int, enum.Enum):
    CAUSAL_EXPERIMENTAL = 1       # Level 1: Causal experimental evidence
    DIRECT_OBSERVATION = 2        # Level 2: Direct experimental observation
    CORRELATIVE = 3               # Level 3: Correlative evidence
    PREDICTED_INFERRED = 4        # Level 4: Predicted / inferred


class ProjectionStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_FEEDBACK = "NEEDS_FEEDBACK"
    REVIEWED = "REVIEWED"
    NOT_RELEVANT = "NOT_RELEVANT"


class FeedbackStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"
    DEV_ISSUE = "DEV_ISSUE"


# ── Research Projects ──────────────────────────────────────────
# One project = one research package (paper + supplementary data).
# Maps to a subfolder under the data root directory.


class ResearchProject(Base):
    __tablename__ = "research_projects"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_path: Mapped[str | None] = mapped_column(
        Text, nullable=True, unique=True,
    )
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
    )
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ProjectGroup(Base):
    """User-defined grouping of research projects (e.g. by topic/field)."""

    __tablename__ = "project_groups"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ProjectAsset(Base):
    __tablename__ = "project_assets"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)


# ── Raw workflow extraction ───────────────────────────────────


class RawWorkflowExtraction(Base):
    """Immutable, paper-level workflow extraction version.

    ``graph`` follows the versioned contract in :mod:`mkb.workflows.contract`.
    A row may move from IN_PROGRESS to COMPLETED/FAILED while it is being
    produced; once completed its graph is never updated in place.
    """

    __tablename__ = "raw_workflow_extractions"

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="IN_PROGRESS"
    )
    record_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="active"
    )
    supersedes_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correction_author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correction_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    review_flags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    graph: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkpoint_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_raw_workflow_project_version"),
        Index("ix_raw_workflow_project_created", "project_id", "created_at"),
    )


class CanonicalWorkflow(Base):
    """Append-only canonicalization derived from one raw extraction version."""

    __tablename__ = "canonical_workflows"

    canonicalization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    raw_extraction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    canonicalizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="IN_PROGRESS")
    graph: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonicalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkpoint_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_canonical_workflow_project_version"),
        Index("ix_canonical_workflow_project_created", "project_id", "created_at"),
        Index("ix_canonical_workflow_raw", "raw_extraction_id"),
    )


class WorkflowSchemaVersion(Base):
    """Immutable snapshot of the curator-managed workflow schema library."""

    __tablename__ = "workflow_schema_versions"
    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SchemaProposal(Base):
    """Evidence-backed, reviewable schema change proposed by the curator."""

    __tablename__ = "schema_proposals"
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    proposal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_workflow_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    analysis: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SchemaProposalRevision(Base):
    """Immutable author-attributed snapshot of a schema proposal draft."""

    __tablename__ = "schema_proposal_revisions"
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_workflow_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    analysis: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    author_type: Mapped[str] = mapped_column(String(32), nullable=False)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_errors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("proposal_id", "revision_number", name="uq_schema_proposal_revision"),
        Index("ix_schema_proposal_revision_history", "proposal_id", "revision_number"),
    )


class WorkflowMaintenanceTask(Base):
    """Auditable request to re-extract or recanonicalize a workflow."""

    __tablename__ = "workflow_maintenance_tasks"
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    source_raw_extraction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_canonicalization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_schema_version: Mapped[str | None] = mapped_column(String(32))
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_workflow_maintenance_status", "status", "task_type", "created_at"),
        Index("ix_workflow_maintenance_project", "project_id", "created_at"),
    )


class WorkflowIndexEntry(Base):
    """Persisted lookup entry derived from one completed canonical workflow."""

    __tablename__ = "workflow_index_entries"
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonicalization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    index_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_label: Mapped[str] = mapped_column(Text, nullable=False)
    target_label: Mapped[str] = mapped_column(Text, nullable=False)
    operation_label: Mapped[str | None] = mapped_column(Text)
    source_schema: Mapped[str | None] = mapped_column(String(64))
    target_schema: Mapped[str | None] = mapped_column(String(64))
    operation_template_id: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    granularity_terms: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    path_node_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    raw_edge_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_workflow_index_direct", "index_type", "source_label", "target_label"),
        Index("ix_workflow_index_template", "source_schema", "operation_template_id", "target_schema"),
        Index("ix_workflow_index_canonical", "canonicalization_id"),
        Index("ix_workflow_index_aliases", "aliases", postgresql_using="gin"),
        Index("ix_workflow_index_granularity", "granularity_terms", postgresql_using="gin"),
    )


# ── Assets (core raw-data table) ───────────────────────────────


class Asset(Base):
    __tablename__ = "assets"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status"),
        default=ProcessingStatus.PENDING,
        nullable=False,
    )
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    embedding: Mapped[list | None] = mapped_column(Vector(1536), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ── Processed Data Layer ─────────────────────────────────────────


class ProcessedAsset(Base):
    __tablename__ = "processed_assets"

    processed_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    processing_type: Mapped[ProcessingType] = mapped_column(
        Enum(ProcessingType, name="processing_type"), nullable=False
    )
    output_format: Mapped[str] = mapped_column(String(50), nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    conversion_metadata: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    raw_asset_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_processed_asset_id", "asset_id"),
        Index("ix_processed_sha256", "sha256"),
    )


class ProcessingLog(Base):
    __tablename__ = "processing_logs"

    log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    processing_type: Mapped[ProcessingType] = mapped_column(
        Enum(ProcessingType, name="processing_type"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    processed_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ── Knowledge Frame ──────────────────────────────────────────────


class KnowledgeFrame(Base):
    __tablename__ = "knowledge_frames"

    frame_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    status: Mapped[FrameStatus] = mapped_column(
        Enum(FrameStatus, name="frame_status"),
        default=FrameStatus.PENDING,
        nullable=False,
    )
    content: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    extraction_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    times_checked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extraction_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_metadata: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    # Persistent agent memory: clarification Q&A history and resolved feedback items.
    # Structure: {"clarifications": [...], "resolved_feedback": [...]}
    agent_annotations: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_frame_project_id", "project_id"),
    )


# ── Extraction Passes ────────────────────────────────────────────


class ExtractionPass(Base):
    __tablename__ = "extraction_passes"

    pass_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    frame_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    pass_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "initial", "review"
    content_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    changes_made: Mapped[dict | None] = mapped_column(JSONB)
    agent_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_extraction_pass_frame_id", "frame_id"),
    )


# ── Spaces (domain-specific extraction configurations) ───────────


class Space(Base):
    __tablename__ = "spaces"

    space_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)

    # What kind of projection this space produces. Common values:
    #   "tabular_database" — list-of-rows for structured DBs (default, legacy)
    #   "qa_benchmark"     — question/answer pairs for evaluation
    #   "skill_cards"      — procedural/skill cards (technique, conditions, success criteria)
    #   "freeform"         — agent-defined arbitrary JSON shape
    purpose: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="tabular_database"
    )

    # The space definition — what to extract
    extraction_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Prompt components
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    field_descriptions: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Optional per-space override of the projection-reviewer prompt.
    # When NULL, the reviewer falls back to a default prompt selected by
    # ``purpose`` (see ``mkb.agents.projection_reviewer``).
    review_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When true (default), running review on this space PRESERVES the prior
    # projection rows by creating a new ``REVIEWED`` projection that
    # supersedes them (history visible via ``include_history``). When false,
    # the legacy behaviour applies: the winner is updated in-place and the
    # losers are soft-deleted (history is lost).
    review_trackable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    # Optional review-time external search. Domain-specific instructions still
    # belong in ``review_prompt``; these fields only control which lookup tools
    # the reviewer is allowed to call.
    review_allow_search: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    review_search_tools: Mapped[list | None] = mapped_column(
        JSONB, nullable=False, server_default=text("'[\"web\"]'::jsonb")
    )
    post_processors: Mapped[list | None] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ── Projections ──────────────────────────────────────────────────


class Projection(Base):
    __tablename__ = "projections"

    projection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    space_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    frame_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Where the projection data came from:
    #   "frame"    — projected from the agent-curated KnowledgeFrame (default)
    #   "markdown" — projected directly from processed-markdown of the project's papers
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="frame"
    )

    status: Mapped[ProjectionStatus] = mapped_column(
        Enum(ProjectionStatus, name="projection_status"),
        default=ProjectionStatus.PENDING,
        nullable=False,
    )

    data: Mapped[dict | None] = mapped_column(JSONB)
    validation_result: Mapped[dict | None] = mapped_column(JSONB)
    agent_notes: Mapped[str | None] = mapped_column(Text)

    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    space_version: Mapped[int] = mapped_column(Integer, nullable=False)

    times_reviewed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # When non-NULL, this projection has been replaced by another (the
    # reviewed version when the space is ``review_trackable``). The pointer
    # is to the newer ``Projection.projection_id``. We do NOT add a FK
    # constraint to keep deletes/migrations simple.
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Reverse pointer: when this projection is the result of a tracked
    # review, ``supersedes_ids`` is the list of older projection_ids it
    # consolidated. Stored as JSONB list-of-strings for simplicity.
    supersedes_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_projection_space_frame", "space_id", "frame_id"),
        Index("ix_projection_deleted_at", "deleted_at"),
        Index("ix_projection_superseded_by", "superseded_by_id"),
    )


# ── Feedback ─────────────────────────────────────────────────────


class Feedback(Base):
    __tablename__ = "feedbacks"

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Source: which projection/agent created this feedback
    source_projection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    source_agent: Mapped[str] = mapped_column(String(100), nullable=False)

    # Target: which frame/project this feedback is about
    target_frame_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # The feedback itself
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # categories: "missing_data", "ambiguous_data", "inconsistency", "wrong_evidence_level", "other"
    field_path: Mapped[str | None] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text)

    # Resolution
    status: Mapped[FeedbackStatus] = mapped_column(
        Enum(FeedbackStatus, name="feedback_status"),
        default=FeedbackStatus.OPEN,
        nullable=False,
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_feedback_target_frame", "target_frame_id"),
        Index("ix_feedback_target_project", "target_project_id"),
        Index("ix_feedback_status", "status"),
    )


# ── Graph Element Reviews ─────────────────────────────────────────


class GraphElementReview(Base):
    __tablename__ = "graph_element_reviews"

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    space_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # "concept" or "relation"
    element_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Normalized concept label, or "src_norm||rel_norm||tgt_norm" for relations
    element_key: Mapped[str] = mapped_column(Text, nullable=False)
    times_examined: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    times_modified: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_examined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("space_id", "element_type", "element_key", name="uq_graph_element_review"),
        Index("ix_graph_element_review_space", "space_id", "element_type"),
    )
