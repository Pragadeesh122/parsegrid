"""ParseGrid — Programmatic JSON merge tasks (Reduce phase).

This is purely deterministic — NO LLM is used in the merge step.
Deduplicates records from overlapping chunks using fingerprinting.
"""

import json
import logging

from app.core.config import settings
from app.services.extraction import merge_extraction_results
from app.worker.celery_app import celery_app
from app.worker.tasks.ocr import _publish_status, _update_job_in_db

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.merge.merge_results",
    bind=True,
    queue="merge",
)
def merge_results(self, chunk_results: list[dict], job_id: str, schema: dict):
    """Merge extracted JSON arrays from all chunks into a single dataset.

    This is the chord callback — it receives all chunk_results automatically.
    Performs fingerprint-based deduplication for records in overlap regions.

    After merging, stores the result and triggers the translation task.
    """
    try:
        _publish_status(job_id, "MERGING", 0.0)
        _update_job_in_db(job_id, status="MERGING", progress=0.0)

        # Merge with deduplication
        merged = merge_extraction_results(chunk_results, schema)

        _publish_status(job_id, "MERGING", 50.0)

        # Store merged result in S3
        from app.core.storage import upload_file_to_s3

        merged_key = f"extracted/{job_id}/merged_data.json"
        upload_file_to_s3(
            file_bytes=json.dumps(merged, indent=2).encode("utf-8"),
            object_key=merged_key,
            content_type="application/json",
        )

        # Update job record with extracted data
        _update_job_in_db(
            job_id,
            status="MERGING",
            progress=80.0,
            extracted_data=json.dumps(merged),
        )

        _publish_status(job_id, "MERGING", 100.0)

        logger.info(f"Job {job_id}: merge complete, triggering translation")

        # Trigger translation task
        from app.worker.tasks.translate import translate_and_provision

        translate_and_provision.apply_async(args=[job_id, merged, schema])

    except Exception as exc:
        logger.exception(f"Job {job_id}: merge failed")
        _publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        _update_job_in_db(job_id, status="FAILED", error_message=str(exc))
        raise
