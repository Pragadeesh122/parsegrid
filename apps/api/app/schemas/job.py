"""ParseGrid API — Pydantic schemas for Job endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus, OutputFormat


# --- Request Schemas ---


class JobCreateRequest(BaseModel):
    """Request body for creating a new extraction job."""

    filename: str = Field(..., min_length=1, max_length=512)
    file_key: str = Field(..., min_length=1, max_length=1024, description="S3 object key")
    file_size: int = Field(..., gt=0)
    output_format: OutputFormat = OutputFormat.SQL


class SchemaApprovalRequest(BaseModel):
    """Request body for approving/editing a proposed schema."""

    locked_schema: dict = Field(..., description="User-approved JSON schema")


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
    output_format: OutputFormat
    progress: float
    proposed_schema: dict | None = None
    locked_schema: dict | None = None
    connection_string: str | None = None
    error_message: str | None = None
    page_count: int | None = None
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


class UploadUrlResponse(BaseModel):
    """Presigned URL for direct client-to-S3 upload."""

    upload_url: str
    file_key: str
