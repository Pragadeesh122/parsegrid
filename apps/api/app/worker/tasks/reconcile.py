"""ParseGrid — Reconciliation task (Phase 7).

Runs normalization, entity resolution, FK resolution, and provenance
attachment on the bucketed extraction output, then dispatches translation
+ provisioning.

Entity resolution uses gpt-5.4 but only fires when the cheap
``needs_resolution()`` pre-check detects duplicate normalized PK tuples
(i.e., the same entity extracted multiple times across chunks). For
documents with clean unique PKs no LLM call is made.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.schemas.extraction_model import DatabaseModel
from app.worker.celery_app import celery_app
from app.worker.db import get_job_field, publish_status, update_job

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.reconcile.reconcile_and_translate",
    bind=True,
    queue="merge",
)
def reconcile_and_translate(
    self,
    job_id: str,
    bucketed_rows: dict[str, list[dict[str, Any]]],
    chunk_pages_by_index: dict[str, dict[int, list[int]]],
):
    """Run deterministic reconciliation and dispatch translate_and_provision."""
    try:
        publish_status(job_id, "RECONCILING", 0.0)
        update_job(job_id, status="RECONCILING", progress=0.0)

        # 1. Load locked_model.
        job = get_job_field(job_id, "locked_model")
        locked_raw = job["locked_model"]
        if isinstance(locked_raw, str):
            locked_raw = json.loads(locked_raw)
        locked_model = DatabaseModel.model_validate(locked_raw)

        # 2. Reconcile. chunk_pages keys come back as strings after JSON
        #    serialization through Celery — coerce them back to ints.
        from app.services.reconciliation import reconcile_model

        normalized_pages = {
            table: {int(k): v for k, v in pages.items()}
            for table, pages in chunk_pages_by_index.items()
        }
        finalized, run_notes = reconcile_model(
            bucketed_rows=bucketed_rows,
            chunk_pages_by_index=normalized_pages,
            locked_model=locked_model,
        )

        publish_status(job_id, "RECONCILING", 60.0)

        # 3. Persist reconciled data on the job.
        update_job(
            job_id,
            status="RECONCILING",
            progress=80.0,
            extracted_data=json.dumps(finalized, default=str),
        )

        # 4. Persist a summary of run notes for debugging.
        from app.core.storage import upload_file_to_s3

        upload_file_to_s3(
            file_bytes=json.dumps(
                {"notes": run_notes, "table_counts": {t: len(r) for t, r in finalized.items()}},
                indent=2,
            ).encode("utf-8"),
            object_key=f"extracted/{job_id}/reconciliation_notes.json",
            content_type="application/json",
        )

        publish_status(job_id, "RECONCILING", 100.0)
        logger.info(
            f"Job {job_id}: reconciliation complete, "
            f"counts={{ {', '.join(f'{t}={len(r)}' for t, r in finalized.items())} }}"
        )

        # 5. Dispatch translate.
        from app.worker.tasks.translate import translate_and_provision

        translate_and_provision.apply_async(args=[job_id])

    except Exception as exc:
        logger.exception(f"Job {job_id}: reconciliation failed")
        publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        update_job(job_id, status="FAILED", error_message=str(exc))
        raise
