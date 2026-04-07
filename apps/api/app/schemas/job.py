"""ParseGrid API — Pydantic schemas for Job endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus, JobType, OutputFormat
from app.schemas.extraction_model import DatabaseModel, DocumentProfile, SectionCandidate

# --- Request Schemas ---


class JobCreateRequest(BaseModel):
    """Request body for creating a new extraction job."""

    filename: str = Field(..., min_length=1, max_length=512)
    file_key: str = Field(..., min_length=1, max_length=1024, description="S3 object key")
    file_size: int = Field(..., gt=0)
    output_format: OutputFormat = OutputFormat.SQL
    job_type: JobType = JobType.FULL


class ModelApprovalRequest(BaseModel):
    """Request body for approving/editing a proposed extraction model (Phase 7)."""

    locked_model: DatabaseModel = Field(
        ..., description="User-approved DatabaseModel (single_table or table_graph)"
    )


class TargetQueryRequest(BaseModel):
    """Request body for submitting a natural language query for targeted extraction."""

    query: str = Field(..., min_length=1, max_length=2000, description="Natural language extraction query")


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
