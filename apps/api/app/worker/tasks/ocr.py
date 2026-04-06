"""ParseGrid — OCR processing tasks.

Uses PaddleOCR (local, air-gapped) via the BaseOCRProvider interface.
After OCR, triggers the Schema Generator Agent via the BaseLLMProvider.
"""

import json
import logging
import os
import tempfile

from app.core.config import settings
from app.worker.celery_app import celery_app
from app.worker.db import get_job_field, publish_status, update_job

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.ocr.process_document",
    bind=True,
    max_retries=3,
    queue="ocr",
)
def process_document(self, job_id: str):
    """OCR processing task using PaddleOCR.

    1. Download file from S3
    2. Run PaddleOCR with layout analysis
    3. Store parsed text in S3
    4. Trigger schema generation
    5. Update job with proposed schema
    """
    try:
        publish_status(job_id, "OCR_PROCESSING", 5.0)
        update_job(job_id, status="OCR_PROCESSING", progress=5.0)

        # 1. Get job details from DB
        job = get_job_field(job_id, "file_key", "filename", "job_type")
        file_key = job["file_key"]
        filename = job["filename"]
        job_type = job["job_type"]

        publish_status(job_id, "OCR_PROCESSING", 10.0)

        # 2. Download file from S3 to temp directory
        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, filename)
            s3.download_file(settings.s3_bucket, file_key, local_path)
            logger.info(f"Downloaded {file_key} → {local_path}")

            publish_status(job_id, "OCR_PROCESSING", 20.0)

            # 3. Run PaddleOCR with layout analysis
            from app.providers.factory import get_ocr_provider

            ocr = get_ocr_provider()
            ocr_result = ocr.process_document(local_path)

            publish_status(job_id, "OCR_PROCESSING", 60.0)
            logger.info(
                f"OCR complete: {ocr_result.page_count} pages, "
                f"{sum(len(p.regions) for p in ocr_result.pages)} regions"
            )

            # 4. Store parsed text in S3
            parsed_key = f"parsed/{job_id}/full_text.txt"
            from app.core.storage import upload_file_to_s3

            upload_file_to_s3(
                file_bytes=ocr_result.full_text.encode("utf-8"),
                object_key=parsed_key,
                content_type="text/plain",
            )

            # Also store structured OCR result as JSON
            import dataclasses

            ocr_json_key = f"parsed/{job_id}/ocr_result.json"
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
                object_key=ocr_json_key,
                content_type="application/json",
            )

            publish_status(job_id, "OCR_PROCESSING", 70.0)

            # 5. Branch based on job_type
            if job_type == "TARGETED":
                # Targeted pipeline: skip schema generation, go to indexing
                update_job(
                    job_id,
                    status="OCR_PROCESSING",
                    progress=75.0,
                    page_count=ocr_result.page_count,
                )
                publish_status(job_id, "OCR_PROCESSING", 75.0)

                from app.worker.tasks.rag import index_document

                index_document.apply_async(args=[job_id])
                logger.info(f"Job {job_id}: OCR complete, dispatched indexing (TARGETED)")

            else:
                # Full pipeline: generate schema proposal
                from app.providers.factory import get_llm_provider

                llm = get_llm_provider()

                sample_text = "\n\n".join(
                    p.full_text for p in ocr_result.pages[:5]
                )
                proposed_schema = llm.generate_schema(sample_text, ocr_result.page_count)

                publish_status(job_id, "SCHEMA_PROPOSED", 90.0)

                update_job(
                    job_id,
                    status="SCHEMA_PROPOSED",
                    progress=100.0,
                    page_count=ocr_result.page_count,
                    proposed_schema=json.dumps(proposed_schema),
                )

                publish_status(job_id, "SCHEMA_PROPOSED", 100.0)
                logger.info(f"Job {job_id}: OCR + schema proposal complete (FULL)")

    except Exception as exc:
        logger.exception(f"Job {job_id}: OCR failed: {exc}")
        publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        update_job(
            job_id,
            status="FAILED",
            error_message=str(exc),
        )
        raise self.retry(exc=exc, countdown=60)
