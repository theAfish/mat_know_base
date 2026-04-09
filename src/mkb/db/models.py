"""SQLAlchemy ORM models for the raw-data layer."""

import enum
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ── Enums ───────────────────────────────────────────────────────
class ProcessingStatus(str, enum.Enum):
    PENDING = "PENDING"
    STORED = "STORED"
    EXTRACTED = "EXTRACTED"
    GRAPHED = "GRAPHED"
    FAILED = "FAILED"


class ProcessingType(str, enum.Enum):
    """Classifies the type of processing applied to raw data."""
    MARKDOWN = "MARKDOWN"  # Textual data converted to markdown
    DATAFRAME = "DATAFRAME"  # Tabular data (CSV, XLSX, etc.)
    IMAGE = "IMAGE"  # Image metadata/analysis
    UNPROCESSABLE = "UNPROCESSABLE"  # Data that couldn't be converted


class ExtractionStatus(str, enum.Enum):
    """Tracks knowledge extraction status at the batch level."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ── Assets (core raw-data table) ───────────────────────────────
class Asset(Base):
    """
    One row per unique file stored in the object store (MinIO).
    Content-addressable: the sha256 column is the canonical identity.
    """

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
    # Domain-specific scientific metadata (lattice params, formula, etc.)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    # Embedding vector (populated later when text is extracted)
    embedding: Mapped[list | None] = mapped_column(Vector(1536), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=lambda: datetime.now(timezone.utc)
    )


# ── Ingestion Batches ──────────────────────────────────────────
class IngestionBatch(Base):
    """Groups related files ingested together (e.g. a paper PDF + CSV data)."""

    __tablename__ = "ingestion_batches"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        Enum(ExtractionStatus, name="extraction_status"),
        default=ExtractionStatus.PENDING,
        nullable=False,
        server_default="PENDING",
    )
    extraction_metadata: Mapped[dict | None] = mapped_column(
        "extraction_metadata", JSONB, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BatchAsset(Base):
    """Association between a batch and its assets."""

    __tablename__ = "batch_assets"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )


# ── Knowledge Nodes (Phase 4 placeholder) ──────────────────────
class KnowledgeNode(Base):
    """
    An entity extracted from one or more assets.
    Placeholder for Phase 4 – Entity & Relationship layer.
    """

    __tablename__ = "knowledge_nodes"

    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    properties: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    embedding: Mapped[list | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeEdge(Base):
    """Directed relationship between two knowledge nodes."""

    __tablename__ = "knowledge_edges"

    edge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # e.g. MEASURED, CITES, SIMULATED_BY
    properties: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_edges_source", "source_node_id"),
        Index("ix_edges_target", "target_node_id"),
        Index("ix_edges_relation", "relation_type"),
    )


# ── Processed Data Layer ─────────────────────────────────────────
class ProcessedAsset(Base):
    """
    Processed version of a raw asset.
    Links back to the original asset via asset_id.
    Stores metadata about the conversion (type, format, etc.).
    """

    __tablename__ = "processed_assets"

    processed_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    processing_type: Mapped[ProcessingType] = mapped_column(
        Enum(ProcessingType, name="processing_type"), nullable=False
    )
    # Output format (e.g. 'md' for markdown, 'parquet' for dataframes, 'json' for metadata)
    output_format: Mapped[str] = mapped_column(String(50), nullable=False)
    # S3 location of processed data
    s3_bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Content-addressed hash of processed data for duplicate detection
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    # Metadata about the conversion (e.g., page count, row count, image dimensions)
    conversion_metadata: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    # Hash of the raw asset at time of conversion (for change detection)
    raw_asset_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_processed_asset_id", "asset_id"),
        Index("ix_processed_sha256", "sha256"),
    )


class ProcessingLog(Base):
    """
    Audit trail: every attempt to process an asset is logged.
    Useful for debugging and understanding conversion history.
    """

    __tablename__ = "processing_logs"

    log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    processing_type: Mapped[ProcessingType] = mapped_column(
        Enum(ProcessingType, name="processing_type"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 'SUCCESS', 'SKIPPED', 'FAILED'
    processed_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
