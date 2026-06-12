"""ParseGrid — OCR processing tasks.

Uses PaddleOCR (local, air-gapped) via the BaseOCRProvider interface.
After OCR completes:
- TARGETED jobs → `index_document` (pgvector indexing)
- FULL jobs    → `profile_and_propose` (whole-document profiling + model discovery)
"""

import json
import logging
import os
import tempfile

from app.core.config import settings
from app.services.safe_errors import public_error_message
from app.worker.celery_app import celery_app
from app.worker.db import (
    get_document_field,
    get_job_field,
    list_job_documents,
    publish_status,
    update_document,
    update_job,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.ocr.process_document",
    bind=True,
    max_retries=3,
    queue="ocr",
)
def process_document(self, job_id: str, document_id: str, append: bool = False):
    """OCR one document of a job.

    1. Download the file from S3
    2. Run the Smart OCR Router
    3. Store full text + structured OCR JSON under parsed/{job_id}/{document_id}/
    4. append=False: return document_id (the creation chord callback advances
       the job). append=True: dispatch single-document extraction directly.
    """
    try:
        update_document(document_id, status="OCR_PROCESSING")
        publish_status(
            job_id, "APPENDING" if append else "OCR_PROCESSING", 5.0, document_id=document_id
        )
        if not append and get_job_field(job_id, "status")["status"] != "FAILED":
            # Guard: a sibling document's terminal failure may have already
            # marked the job FAILED — never resurrect it to OCR_PROCESSING
            # (the chord callback will not fire, so it would stick forever).
            update_job(job_id, status="OCR_PROCESSING", progress=5.0)

        doc = get_document_field(document_id, "file_key", "filename")
        file_key = doc["file_key"]
        filename = doc["filename"]

        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, filename)
            s3.download_file(settings.s3_bucket, file_key, local_path)
            logger.info(f"Downloaded {file_key} → {local_path}")

            from app.providers.factory import get_ocr_provider

            ocr = get_ocr_provider()
            ocr_result = ocr.process_document(local_path)

            logger.info(
                f"OCR complete: {ocr_result.page_count} pages, "
                f"{sum(len(p.regions) for p in ocr_result.pages)} regions"
            )

            prefix = f"parsed/{job_id}/{document_id}"
            from app.core.storage import upload_file_to_s3

            upload_file_to_s3(
                file_bytes=ocr_result.full_text.encode("utf-8"),
                object_key=f"{prefix}/full_text.txt",
                content_type="text/plain",
            )

            import dataclasses

            ocr_data = {
                "page_count": ocr_result.page_count,
                "pages": [
                    {
                        "page_number": p.page_number,
                        "width": p.width,
                        "height": p.height,
                        "regions": [dataclasses.asdict(r) for r in p.regions],
                    }
                    for p in ocr_result.pages
                ],
            }
            upload_file_to_s3(
                file_bytes=json.dumps(ocr_data, indent=2).encode("utf-8"),
                object_key=f"{prefix}/ocr_result.json",
                content_type="application/json",
            )

        update_document(document_id, status="OCR_DONE", page_count=ocr_result.page_count)
        publish_status(
            job_id, "APPENDING" if append else "OCR_PROCESSING", 60.0, document_id=document_id
        )

        if append:
            from app.worker.tasks.extract import run_extraction

            run_extraction.apply_async(args=[job_id], kwargs={"document_id": document_id})
            logger.info(
                f"Job {job_id}: append OCR done, dispatched extraction for document {document_id}"
            )
        return document_id

    except Exception as exc:
        logger.exception(f"Job {job_id} document {document_id}: OCR failed: {exc}")
        update_document(document_id, status="FAILED", error_message=public_error_message(exc))
        if append:
            # Failure isolation: the dataset stays live. Swallow (don't
            # re-raise) so the task_failure signal cannot overwrite the
            # COMPLETED status back to FAILED; hard crashes still bypass
            # this handler and reach the signal, where FAILED is the
            # recoverable answer (rebuild endpoint).
            update_job(job_id, status="COMPLETED", progress=100.0)
            publish_status(
                job_id,
                "COMPLETED",
                100.0,
                error_message=public_error_message(exc),
                document_id=document_id,
            )
            return None
        publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
        update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(
    name="app.worker.tasks.ocr.ocr_complete",
    bind=True,
    queue="ocr",
)
def ocr_complete(self, document_ids: list[str], job_id: str):
    """Chord callback after every founding document finishes OCR.

    Routes FULL jobs to multi-document profiling and TARGETED jobs to RAG
    indexing (TARGETED is validated single-document at creation).
    """
    try:
        job = get_job_field(job_id, "job_type")
        total_pages = sum(d["page_count"] or 0 for d in list_job_documents(job_id, "page_count"))
        update_job(job_id, status="OCR_PROCESSING", progress=75.0, page_count=total_pages)
        publish_status(job_id, "OCR_PROCESSING", 75.0)

        if job["job_type"] == "TARGETED":
            from app.worker.tasks.rag import index_document

            index_document.apply_async(args=[job_id, document_ids[0]])
            logger.info(f"Job {job_id}: OCR complete, dispatched indexing (TARGETED)")
        else:
            from app.worker.tasks.profile import profile_and_propose

            profile_and_propose.apply_async(args=[job_id])
            logger.info(f"Job {job_id}: OCR complete, dispatched profiling (FULL)")
    except Exception as exc:
        logger.exception(f"Job {job_id}: ocr_complete failed")
        publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
        update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise
