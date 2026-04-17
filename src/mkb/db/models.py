"""SQLAlchemy ORM models."""

import enum
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, Index, Integer, String, Text, func
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
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_metadata: Mapped[dict | None] = mapped_column(JSONB, default=dict)

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
