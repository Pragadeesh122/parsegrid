"""ParseGrid API — Job CRUD and lifecycle endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.security import TokenPayload
from app.models.job import Job, JobStatus
from app.schemas.job import (
    DataPreviewResponse,
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


@router.post(
    "/{job_id}/reject-schema",
    response_model=JobResponse,
    summary="Reject the proposed schema and re-trigger OCR",
)
async def reject_schema(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Reject the AI-proposed schema, reset the job, and re-run OCR + schema discovery."""
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (JobStatus.SCHEMA_PROPOSED, JobStatus.AWAITING_REVIEW):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject schema in status {job.status}",
        )

    job.proposed_schema = None
    job.status = JobStatus.UPLOADED
    job.progress = 0.0
    await db.commit()
    await db.refresh(job)

    # Re-enqueue OCR processing
    from app.worker.tasks.ocr import process_document

    process_document.apply_async(args=[job.id])

    return job


@router.get(
    "/{job_id}/data-preview",
    response_model=DataPreviewResponse,
    summary="Get a paginated preview of extracted data",
)
async def get_data_preview(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return the first 20 records from extracted_data plus the total count.

    The full extracted_data payload can be very large, so this endpoint
    provides a lightweight preview for the frontend to render on completion.
    """
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job.extracted_data:
        raise HTTPException(status_code=400, detail="No extracted data available")

    data = job.extracted_data
    if isinstance(data, str):
        import json
        data = json.loads(data)

    # Find the items array — it may be at the top level or nested under a key
    items: list = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Look for a list-valued key (commonly "items", "records", "rows")
        for key in ("items", "records", "rows", "data"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        if not items:
            # Fallback: find the first list-valued key
            for val in data.values():
                if isinstance(val, list):
                    items = val
                    break

    # Extract column names from the first record
    columns: list[str] = []
    if items and isinstance(items[0], dict):
        columns = list(items[0].keys())

    return {
        "total_records": len(items),
        "preview": items[:20],
        "columns": columns,
    }
