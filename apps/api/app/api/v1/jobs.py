"""ParseGrid API — Job CRUD and lifecycle endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.security import TokenPayload
from app.core.storage import delete_object_from_s3, delete_prefix_from_s3
from app.models.job import Job, JobStatus, JobType
from app.providers.factory import get_output_provider
from app.schemas.job import (
    DataPreviewResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobStatusResponse,
    ModelApprovalRequest,
    TargetQueryRequest,
)

router = APIRouter(prefix="/jobs", tags=["Jobs"])

DELETABLE_JOB_STATUSES = {
    JobStatus.MODEL_PROPOSED,
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

    upload_prefix = (
        f"{job.file_key.rsplit('/', 1)[0]}/"
        if "/" in job.file_key
        else None
    )
    if upload_prefix:
        delete_prefix_from_s3(upload_prefix)
    else:
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
    "/{job_id}/approve-model",
    response_model=JobResponse,
    summary="Approve or edit the proposed extraction model (Phase 7)",
)
async def approve_model(
    job_id: str,
    body: ModelApprovalRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Validate and lock the user-approved DatabaseModel, then begin extraction."""
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (JobStatus.MODEL_PROPOSED, JobStatus.AWAITING_REVIEW):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve model in status {job.status}",
        )

    # Server-side normalization + structural validation. Downgraded
    # relationships are preserved (enabled=False) so the user sees what was
    # rejected. Identifier errors raise ValueError → 422.
    from app.services.ddl import validate_model

    try:
        validation = validate_model(body.locked_model)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid model: {e}") from e

    job.locked_model = validation.model.model_dump()
    job.status = JobStatus.MODEL_LOCKED
    job.progress = 0.0
    await db.commit()
    await db.refresh(job)

    # Enqueue extraction pipeline (per-table Map-Reduce).
    from app.worker.tasks.extract import run_extraction

    run_extraction.apply_async(args=[job.id])

    return job


@router.post(
    "/{job_id}/reject-model",
    response_model=JobResponse,
    summary="Reject the proposed model and reset the job (Phase 7)",
)
async def reject_model(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Discard the AI-proposed DatabaseModel and re-run discovery from scratch."""
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (JobStatus.MODEL_PROPOSED, JobStatus.AWAITING_REVIEW):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject model in status {job.status}",
        )

    job.proposed_model = None
    job.document_profile = None
    job.section_map = None
    job.progress = 0.0

    # FULL jobs re-run OCR → profiling → discovery.
    # TARGETED jobs return to AWAITING_QUERY so the user can issue a new query.
    if job.job_type == JobType.TARGETED:
        job.status = JobStatus.AWAITING_QUERY
        await db.commit()
        await db.refresh(job)
        return job

    job.status = JobStatus.UPLOADED
    await db.commit()
    await db.refresh(job)

    from app.worker.tasks.ocr import process_document

    process_document.apply_async(args=[job.id])

    return job


@router.get(
    "/{job_id}/data-preview",
    response_model=DataPreviewResponse,
    summary="Get a multi-table preview of extracted data (Phase 7)",
)
async def get_data_preview(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return per-table previews (first 20 rows + counts + columns).

    Phase 7 shape: extracted_data is `{table_name: [row, ...]}`. Columns
    are taken from the locked_model so empty tables still report a header.
    """
    import json

    from app.schemas.extraction_model import DatabaseModel

    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job.extracted_data:
        raise HTTPException(status_code=400, detail="No extracted data available")

    data = job.extracted_data
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail="extracted_data is not a multi-table dict (Phase 7 shape)",
        )

    # Pull declared columns from locked_model so empty tables still show headers.
    locked_raw = job.locked_model
    if isinstance(locked_raw, str):
        locked_raw = json.loads(locked_raw)
    declared_columns: dict[str, list[str]] = {}
    if locked_raw:
        try:
            locked_model = DatabaseModel.model_validate(locked_raw)
            declared_columns = {
                t.table_name: [c.name for c in t.columns] for t in locked_model.tables
            }
        except Exception:
            declared_columns = {}

    tables: dict[str, dict] = {}
    for table_name, rows in data.items():
        if not isinstance(rows, list):
            continue
        columns = declared_columns.get(table_name) or (
            list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        )
        tables[table_name] = {
            "total_records": len(rows),
            "preview": rows[:20],
            "columns": columns,
        }

    return {"tables": tables}


@router.post(
    "/{job_id}/target-query",
    response_model=JobResponse,
    summary="Submit a targeted extraction query (Phase 7)",
)
async def target_query(
    job_id: str,
    body: TargetQueryRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Submit a natural language query for targeted RAG extraction.

    Embeds the query, retrieves the top-K most relevant chunks via pgvector
    cosine similarity, asks the LLM to propose a Phase 7 DatabaseModel from
    those chunks, and stores both the model and the retrieved chunks.
    """
    import json

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

    # 1. Embed the user's query.
    from app.providers.factory import get_embedding_provider, get_llm_provider

    embedder = get_embedding_provider()
    query_embedding = embedder.embed_query(body.query)
    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    # 2. Retrieve top-K chunks via cosine similarity.
    #    The embedding column is vector(3072), but pgvector caps full-precision
    #    HNSW at 2000 dims, so the index lives on the halfvec(3072) cast. The
    #    ORDER BY expression here mirrors the index expression so the planner
    #    can serve an ANN lookup; the SELECT keeps full-precision similarity
    #    for the returned score.
    chunk_result = await db.execute(
        sql_text(
            "SELECT chunk_text, page_number, "
            "1 - (embedding <=> CAST(:query_embedding AS vector)) AS similarity "
            "FROM document_chunks "
            "WHERE job_id = :job_id "
            "ORDER BY embedding::halfvec(3072) "
            "         <=> CAST(:query_embedding AS vector)::halfvec(3072) "
            "LIMIT 10"
        ),
        {"job_id": job_id, "query_embedding": embedding_str},
    )
    rows = chunk_result.fetchall()

    if not rows:
        raise HTTPException(status_code=400, detail="No indexed chunks found for this job")

    # 3. Build context. The "--- Page N ---" markers match the format the
    #    chunker expects so downstream extraction can recover page numbers.
    retrieved_chunks = []
    context_parts = []
    for row in rows:
        chunk_text_value, page_number, similarity = row
        retrieved_chunks.append(
            {
                "text": chunk_text_value,
                "page_number": page_number,
                "similarity": float(similarity),
            }
        )
        context_parts.append(f"--- Page {page_number} ---\n{chunk_text_value}")

    context_text = "\n\n".join(context_parts)

    # 4. Ask the LLM to propose a DatabaseModel from the retrieved context.
    #    No DocumentProfile for TARGETED — the retrieval acts as the profile.
    llm = get_llm_provider()
    proposed_model = llm.generate_model(
        document_text=context_text,
        profile=None,
        num_pages=len(rows),
    )

    # 5. Persist target chunks + proposed model + transition.
    job.target_chunks = retrieved_chunks
    job.proposed_model = proposed_model.model_dump()
    job.status = JobStatus.MODEL_PROPOSED
    job.progress = 100.0
    await db.commit()
    await db.refresh(job)

    # 6. Publish SSE event so the UI flips into review.
    from app.worker.db import _get_redis_client

    r = _get_redis_client()
    channel = f"job:{job_id}:status"
    r.publish(
        channel,
        json.dumps({"status": "MODEL_PROPOSED", "progress": 100.0}),
    )

    return job
