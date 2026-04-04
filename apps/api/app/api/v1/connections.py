"""ParseGrid API — Connection management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.security import TokenPayload
from app.models.job import Job, JobStatus
from app.schemas.job import JobResponse

router = APIRouter(tags=["Connections"])


# --- Request/Response schemas ---


class ConnectionTestRequest(BaseModel):
    """Request body for testing a database connection."""

    connection_string: str
    output_format: str = "SQL"


class ConnectionTestResponse(BaseModel):
    """Response from a connection test."""

    success: bool
    message: str


# --- Endpoints ---


@router.post(
    "/connections/test",
    response_model=ConnectionTestResponse,
    summary="Test a database connection string",
)
async def test_connection(
    body: ConnectionTestRequest,
    user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Verify that a connection string is valid and the database is reachable.

    Uses the appropriate output provider to attempt a lightweight connection.
    """
    from app.providers.factory import get_output_provider

    try:
        provider = get_output_provider(body.output_format)
    except ValueError as e:
        return {"success": False, "message": str(e)}

    try:
        provider.test_connection(body.connection_string)
        return {"success": True, "message": "Connection successful"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {e}"}


@router.get(
    "/jobs/{job_id}/connection",
    response_model=JobResponse,
    summary="Get connection string for a completed job",
)
async def get_connection_string(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Returns the full job with connection string for a completed extraction job."""
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Connection string not available. Job status: {job.status}",
        )

    if not job.connection_string:
        raise HTTPException(
            status_code=500,
            detail="Job completed but connection string was not generated",
        )

    return job
