"""SQLAlchemy ORM models."""

import enum
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, Index, Integer, String, Text, UniqueConstraint, func
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
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)

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

    # The space definition — what to extract
    extraction_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Prompt components
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    field_descriptions: Mapped[dict] = mapped_column(JSONB, nullable=False)

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

