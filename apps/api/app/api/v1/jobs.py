"""ParseGrid API — Job CRUD and lifecycle endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.storage import delete_object_from_s3, delete_prefix_from_s3
from app.core.security import TokenPayload
from app.models.job import Job, JobStatus
from app.providers.factory import get_output_provider
from app.schemas.job import (
    DataPreviewResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobStatusResponse,
    SchemaApprovalRequest,
    TargetQueryRequest,
)

router = APIRouter(prefix="/jobs", tags=["Jobs"])

DELETABLE_JOB_STATUSES = {
    JobStatus.SCHEMA_PROPOSED,
    JobStatus.AWAITING_REVIEW,
    JobStatus.AWAITING_QUERY,
    JobStatus.COMPLETED,
    JobStatus.FAILED,
}


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
        job_type=body.job_type,
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


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a job and all persisted artifacts",
)
async def delete_job(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a completed or idle job and all persisted artifacts."""
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in DELETABLE_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete a job while it is processing ({job.status}). "
                "Wait for it to finish or fail first."
            ),
        )

    delete_object_from_s3(job.file_key)
    delete_prefix_from_s3(f"parsed/{job_id}/")
    delete_prefix_from_s3(f"extracted/{job_id}/")

    output_format = (
        job.output_format.value
        if hasattr(job.output_format, "value")
        else str(job.output_format)
    )
    if job.output_schema_name and output_format == "SQL":
        provider = get_output_provider(output_format)
        provider.delete_output(job.output_schema_name)

    await db.delete(job)
    await db.commit()


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


@router.post(
    "/{job_id}/target-query",
    response_model=JobResponse,
    summary="Submit a targeted extraction query",
)
async def target_query(
    job_id: str,
    body: TargetQueryRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Submit a natural language query for targeted RAG extraction.

    Embeds the query, retrieves the top 10 most relevant chunks via
    pgvector cosine similarity, generates a schema from those chunks,
    and stores the target chunks for subsequent extraction.
    """
    from sqlalchemy import text as sql_text

    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.AWAITING_QUERY:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot submit query in status {job.status}. Job must be in AWAITING_QUERY.",
        )

    # 1. Embed the user's query
    from app.providers.factory import get_embedding_provider

    embedder = get_embedding_provider()
    query_embedding = embedder.embed_query(body.query)
    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    # 2. Retrieve top 10 chunks via cosine similarity
    chunk_result = await db.execute(
        sql_text(
            "SELECT chunk_text, page_number, 1 - (embedding <=> CAST(:query_embedding AS vector)) AS similarity "
            "FROM document_chunks "
            "WHERE job_id = :job_id "
            "ORDER BY embedding <=> CAST(:query_embedding AS vector) "
            "LIMIT 10"
        ),
        {"job_id": job_id, "query_embedding": embedding_str},
    )
    rows = chunk_result.fetchall()

    if not rows:
        raise HTTPException(status_code=400, detail="No indexed chunks found for this job")

    # 3. Build context from retrieved chunks
    retrieved_chunks = []
    context_parts = []
    for row in rows:
        chunk_text, page_number, similarity = row
        retrieved_chunks.append({
            "text": chunk_text,
            "page_number": page_number,
            "similarity": float(similarity),
        })
        context_parts.append(f"[Page {page_number}]\n{chunk_text}")

    context_text = "\n\n---\n\n".join(context_parts)

    # 4. Generate schema from retrieved chunks
    from app.providers.factory import get_llm_provider

    llm = get_llm_provider()
    proposed_schema = llm.generate_schema(context_text, len(rows))

    # 5. Store target chunks and schema on the job
    import json

    job.target_chunks = retrieved_chunks
    job.proposed_schema = proposed_schema if isinstance(proposed_schema, dict) else json.loads(proposed_schema)
    job.status = JobStatus.SCHEMA_PROPOSED
    job.progress = 100.0
    await db.commit()
    await db.refresh(job)

    # 6. Publish SSE event
    from app.worker.db import _get_redis_client

    r = _get_redis_client()
    channel = f"job:{job_id}:status"
    r.publish(channel, json.dumps({"status": "SCHEMA_PROPOSED", "progress": 100.0}))

    return job
