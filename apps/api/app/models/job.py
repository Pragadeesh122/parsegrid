"""ParseGrid API — Job model.

Represents an extraction job lifecycle from upload to completion.
"""

import enum
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, generate_uuid


class JobStatus(str, enum.Enum):
    """Job lifecycle states (matches state machine in implementation plan)."""

    UPLOADED = "UPLOADED"
    OCR_PROCESSING = "OCR_PROCESSING"
    INDEXING = "INDEXING"
    AWAITING_QUERY = "AWAITING_QUERY"
    SCHEMA_PROPOSED = "SCHEMA_PROPOSED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    SCHEMA_LOCKED = "SCHEMA_LOCKED"
    EXTRACTING = "EXTRACTING"
    MERGING = "MERGING"
    TRANSLATING = "TRANSLATING"
    PROVISIONING = "PROVISIONING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobType(str, enum.Enum):
    """Extraction mode: full document vs targeted RAG."""

    FULL = "FULL"
    TARGETED = "TARGETED"


class OutputFormat(str, enum.Enum):
    """Supported output database formats."""

    SQL = "SQL"
    GRAPH = "GRAPH"
    VECTOR = "VECTOR"


class Job(Base, TimestampMixin):
    """Extraction job tracking table in the internal metadata database."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="User sub claim from Auth.js JWT",
    )
    filename: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    file_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment="S3 object key for the uploaded file",
    )
    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", create_constraint=True),
        nullable=False,
        default=JobStatus.UPLOADED,
        index=True,
    )
    job_type: Mapped[JobType] = mapped_column(
        Enum(JobType, name="job_type", create_constraint=True),
        nullable=False,
        default=JobType.FULL,
    )
    output_format: Mapped[OutputFormat] = mapped_column(
        Enum(OutputFormat, name="output_format", create_constraint=True),
        nullable=False,
        default=OutputFormat.SQL,
    )
    progress: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="Progress percentage 0-100",
    )
    proposed_schema: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="AI-proposed JSON schema (Phase 1 discovery)",
    )
    locked_schema: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="User-approved JSON schema (Phase 2 validation)",
    )
    extracted_data: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Merged extraction result (Phase 3 execution)",
    )
    output_schema_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="PostgreSQL schema name: job_{uuid}",
    )
    connection_string: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Connection string delivered to user on completion",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Total pages detected by OCR",
    )
    provisioned_rows: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of rows inserted into target database",
    )
    provisioned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when provisioning completed",
    )
    target_ddl: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Generated DDL stored for audit trail",
    )
    target_chunks: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Retrieved chunk texts for targeted extraction",
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Job(id={self.id!r}, status={self.status!r}, filename={self.filename!r})>"


class DocumentChunk(Base):
    """Vector-indexed document chunk for RAG-based targeted extraction."""

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    chunk_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(3072),
        nullable=True,
        comment="3072-dim vector from text-embedding-3-large",
    )

    job: Mapped["Job"] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<DocumentChunk(id={self.id!r}, job_id={self.job_id!r}, chunk_index={self.chunk_index})>"
