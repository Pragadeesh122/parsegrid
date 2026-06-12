"""ParseGrid API — Document endpoints (Dataset Consolidation).

Append a file to a completed dataset, resolve a paused append, delete a
document (with rebuild). All routes are user-scoped through the parent job.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.security import TokenPayload
from app.core.storage import delete_object_from_s3, delete_prefix_from_s3
from app.models.job import Document, DocumentStatus, Job, JobStatus, JobType
from app.schemas.job import DocumentCreateRequest, DocumentResponse, ResolveAppendRequest

router = APIRouter(prefix="/jobs/{job_id}/documents", tags=["Documents"])


async def _owned_job(job_id: str, user: TokenPayload, db: AsyncSession) -> Job:
    result = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.sub))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _document_on(job: Job, document_id: str) -> Document:
    for document in job.documents:
        if document.id == document_id:
            return document
    raise HTTPException(status_code=404, detail="Document not found")


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Append a file to a completed dataset",
)
async def append_document(
    job_id: str,
    body: DocumentCreateRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Document:
    job = await _owned_job(job_id, user, db)
    if job.job_type == JobType.TARGETED:
        raise HTTPException(status_code=400, detail="TARGETED jobs cannot be appended to")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Job must be COMPLETED to append (currently {job.status})",
        )

    document = Document(
        id=str(uuid.uuid4()),
        job_id=job.id,
        filename=body.filename,
        file_key=body.file_key,
        file_size=body.file_size,
    )
    db.add(document)
    job.status = JobStatus.APPENDING
    job.progress = 0.0
    await db.commit()
    await db.refresh(document)

    from app.worker.tasks.ocr import process_document

    process_document.apply_async(args=[job.id, document.id], kwargs={"append": True})

    return document


@router.post(
    "/{document_id}/resolve",
    response_model=DocumentResponse,
    summary="Force or cancel an append paused at the compatibility gate",
)
async def resolve_append(
    job_id: str,
    document_id: str,
    body: ResolveAppendRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Document:
    job = await _owned_job(job_id, user, db)
    if job.status != JobStatus.AWAITING_APPEND_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"No append awaiting review (job is {job.status})",
        )
    document = _document_on(job, document_id)
    if not (document.compat_report or {}).get("needs_review"):
        raise HTTPException(status_code=400, detail="This document is not awaiting review")

    # Stamp the resolution so a later append's review cannot be hijacked by
    # this document's stale needs_review flag (JSON column: reassign, don't
    # mutate in place, or SQLAlchemy won't detect the change).
    document.compat_report = {
        **document.compat_report,
        "needs_review": False,
        "resolved": body.action,
    }

    if body.action == "cancel":
        document.status = DocumentStatus.REJECTED
        job.status = JobStatus.COMPLETED
        job.progress = 100.0
        await db.commit()
        await db.refresh(document)
        return document

    # force: proceed with what mapped — rebuild from all EXTRACTED documents.
    job.status = JobStatus.RECONCILING
    job.progress = 0.0
    await db.commit()
    await db.refresh(document)

    from app.worker.tasks.reconcile import rebuild_dataset

    rebuild_dataset.apply_async(args=[job.id])

    return document


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a document from a dataset and rebuild the output",
)
async def delete_document(
    job_id: str,
    document_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    job = await _owned_job(job_id, user, db)
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Job must be COMPLETED to remove documents (currently {job.status})",
        )
    document = _document_on(job, document_id)
    contributing = [d for d in job.documents if d.status == DocumentStatus.EXTRACTED]
    needs_rebuild = document.status == DocumentStatus.EXTRACTED
    if needs_rebuild and len(contributing) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove the last contributing document — delete the job instead",
        )

    upload_prefix = f"{document.file_key.rsplit('/', 1)[0]}/" if "/" in document.file_key else None
    if upload_prefix:
        delete_prefix_from_s3(upload_prefix)
    else:
        delete_object_from_s3(document.file_key)
    delete_prefix_from_s3(f"parsed/{job_id}/{document_id}/")

    await db.delete(document)
    if needs_rebuild:
        job.status = JobStatus.RECONCILING
        job.progress = 0.0
    await db.commit()

    if needs_rebuild:
        from app.worker.tasks.reconcile import rebuild_dataset

        rebuild_dataset.apply_async(args=[job_id])
