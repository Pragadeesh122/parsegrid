"""ParseGrid API — Job CRUD and lifecycle endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.security import TokenPayload
from app.models.job import Job, JobStatus
from app.schemas.job import (
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobStatusResponse,
    SchemaApprovalRequest,
)

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new extraction job",
)
async def create_job(
    body: JobCreateRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Create a new extraction job after the file has been uploaded to S3."""
    job = Job(
        id=str(uuid.uuid4()),
        user_id=user.sub,
        filename=body.filename,
        file_key=body.file_key,
        file_size=body.file_size,
        output_format=body.output_format,
        status=JobStatus.UPLOADED,
        progress=0.0,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue OCR processing task via Celery
    from app.worker.tasks.ocr import process_document

    process_document.apply_async(args=[job.id])

    return job


@router.get(
    "",
    response_model=JobListResponse,
    summary="List all jobs for the current user",
)
async def list_jobs(
    skip: int = 0,
    limit: int = 20,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """List jobs belonging to the authenticated user, newest first."""
    query = (
        select(Job)
        .where(Job.user_id == user.sub)
        .order_by(Job.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    jobs = result.scalars().all()

    count_query = select(func.count()).select_from(Job).where(Job.user_id == user.sub)
    total = (await db.execute(count_query)).scalar() or 0

    return {"jobs": jobs, "total": total}


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get a specific job",
)
async def get_job(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Fetch a single job by ID. Only the owning user can access it."""
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get(
    "/{job_id}/status",
    response_model=JobStatusResponse,
    summary="Get lightweight job status (for polling)",
)
async def get_job_status(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Lightweight status endpoint for TanStack Query polling fallback."""
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post(
    "/{job_id}/approve-schema",
    response_model=JobResponse,
    summary="Approve or edit the proposed schema",
)
async def approve_schema(
    job_id: str,
    body: SchemaApprovalRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Lock the user-approved schema and begin extraction."""
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (JobStatus.SCHEMA_PROPOSED, JobStatus.AWAITING_REVIEW):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve schema in status {job.status}",
        )

    job.locked_schema = body.locked_schema
    job.status = JobStatus.SCHEMA_LOCKED
    await db.commit()
    await db.refresh(job)

    # Enqueue extraction pipeline (Map-Reduce)
    from app.worker.tasks.extract import run_extraction

    run_extraction.apply_async(args=[job.id])

    return job
