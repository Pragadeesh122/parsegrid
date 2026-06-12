"""ParseGrid API — Pydantic schemas for Job endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.job import DocumentStatus, JobStatus, JobType, OutputFormat
from app.schemas.extraction_model import DatabaseModel, DocumentProfile, SectionCandidate

# --- Request Schemas ---


class JobFileSpec(BaseModel):
    """One uploaded file inside a job-creation request."""

    filename: str = Field(..., min_length=1, max_length=512)
    file_key: str = Field(..., min_length=1, max_length=1024, description="S3 object key")
    file_size: int = Field(..., gt=0)


class JobCreateRequest(BaseModel):
    """Request body for creating a new extraction job.

    Accepts either the multi-file `files` list or the legacy single-file
    triple (filename/file_key/file_size); the legacy shape is normalized
    into a one-element `files` list.
    """

    files: list[JobFileSpec] | None = Field(default=None, min_length=1, max_length=50)
    filename: str | None = Field(default=None, min_length=1, max_length=512)
    file_key: str | None = Field(default=None, min_length=1, max_length=1024)
    file_size: int | None = Field(default=None, gt=0)
    output_format: OutputFormat = OutputFormat.SQL
    job_type: JobType = JobType.FULL

    @model_validator(mode="after")
    def _normalize_files(self) -> "JobCreateRequest":
        if self.files is None:
            if not (self.filename and self.file_key and self.file_size):
                raise ValueError("provide either files[] or filename/file_key/file_size")
            self.files = [
                JobFileSpec(
                    filename=self.filename,
                    file_key=self.file_key,
                    file_size=self.file_size,
                )
            ]
        return self


class ModelApprovalRequest(BaseModel):
    """Request body for approving/editing a proposed extraction model (Phase 7)."""

    locked_model: DatabaseModel = Field(
        ..., description="User-approved DatabaseModel (single_table or table_graph)"
    )


class TargetQueryRequest(BaseModel):
    """Request body for submitting a natural language query for targeted extraction."""

    query: str = Field(
        ..., min_length=1, max_length=2000, description="Natural language extraction query"
    )


class DocumentResponse(BaseModel):
    """Per-file state inside a dataset job."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    filename: str
    file_key: str
    file_size: int
    page_count: int | None = None
    status: DocumentStatus
    compat_report: dict | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentCreateRequest(JobFileSpec):
    """Request body for appending a file to a completed dataset."""


class ResolveAppendRequest(BaseModel):
    """Force/cancel decision for an append that paused at the compat gate."""

    action: Literal["force", "cancel"]


# --- Response Schemas ---


class JobResponse(BaseModel):
    """Full job representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    filename: str
    file_key: str
    file_size: int
    status: JobStatus
    job_type: JobType
    output_format: OutputFormat
    progress: float
    document_profile: DocumentProfile | None = None
    proposed_model: DatabaseModel | None = None
    locked_model: DatabaseModel | None = None
    section_map: list[SectionCandidate] | None = None
    connection_string: str | None = None
    error_message: str | None = None
    page_count: int | None = None
    provisioned_rows: int | None = None
    provisioned_at: datetime | None = None
    target_ddl: str | None = None
    created_at: datetime
    updated_at: datetime
    documents: list[DocumentResponse] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def document_count(self) -> int:
        return len(self.documents)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_pages(self) -> int | None:
        pages = [d.page_count for d in self.documents if d.page_count]
        return sum(pages) if pages else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_file_size(self) -> int:
        return sum(d.file_size for d in self.documents)


class JobListResponse(BaseModel):
    """Paginated list of jobs."""

    jobs: list[JobResponse]
    total: int


class JobStatusResponse(BaseModel):
    """Lightweight status response for polling / SSE."""

    id: str
    status: JobStatus
    progress: float
    error_message: str | None = None
    connection_string: str | None = None


class TablePreview(BaseModel):
    """Preview payload for one table inside a multi-table extraction result."""

    total_records: int
    preview: list[dict]
    columns: list[str]


class DataPreviewResponse(BaseModel):
    """Multi-table preview of extracted data for the frontend (Phase 7).

    The full extracted_data can be megabytes — each table returns only the
    first 20 records plus the total count and column names.
    """

    tables: dict[str, TablePreview]


class UploadUrlResponse(BaseModel):
    """Presigned URL for direct client-to-S3 upload."""

    upload_url: str
    file_key: str
