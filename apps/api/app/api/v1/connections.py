"""ParseGrid API — Connection string delivery endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.security import TokenPayload
from app.models.job import Job, JobStatus
from app.schemas.job import JobResponse

router = APIRouter(prefix="/jobs", tags=["Connections"])


@router.get(
    "/{job_id}/connection",
    response_model=JobResponse,
    summary="Get connection string for a completed job",
)
async def get_connection_string(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Returns the connection string for a completed extraction job.
    Only available when job status is COMPLETED.
    """
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
