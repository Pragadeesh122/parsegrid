"""ParseGrid — Dataset rebuild task (Dataset Consolidation).

Unions every EXTRACTED document's stored buckets, runs deterministic
reconciliation over the union, persists the reconciled extracted_data on the
job, and dispatches translate_and_provision (which drop-recreates the output
schema). The output database is a cache; per-document buckets are the truth.

Legacy jobs (pre-consolidation) stored job-level buckets in S3 at
extracted/{job_id}/merged_buckets.json — when an EXTRACTED document has no
stored buckets, that file is adopted as the founding document's buckets.
"""

from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.schemas.extraction_model import DatabaseModel
from app.services.safe_errors import public_error_message
from app.worker.celery_app import celery_app
from app.worker.db import (
    get_job_field,
    list_job_documents,
    publish_status,
    update_document,
    update_job,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.reconcile.rebuild_dataset",
    bind=True,
    queue="merge",
)
def rebuild_dataset(self, job_id: str):
    """Re-reconcile the union of all EXTRACTED documents and re-provision."""
    try:
        publish_status(job_id, "RECONCILING", 0.0)
        update_job(job_id, status="RECONCILING", progress=0.0)

        job = get_job_field(job_id, "locked_model")
        locked_raw = job["locked_model"]
        if isinstance(locked_raw, str):
            locked_raw = json.loads(locked_raw)
        if not locked_raw:
            raise ValueError("locked_model missing — cannot rebuild")
        locked_model = DatabaseModel.model_validate(locked_raw)

        doc_buckets = _load_document_buckets(job_id)
        if not doc_buckets:
            raise ValueError("no extracted documents with buckets — nothing to rebuild")

        from app.services.consolidation import union_buckets
        from app.services.reconciliation import reconcile_model

        bucketed_rows, chunk_pages = union_buckets(doc_buckets)
        finalized, run_notes = reconcile_model(
            bucketed_rows=bucketed_rows,
            chunk_pages_by_index=chunk_pages,
            locked_model=locked_model,
        )

        publish_status(job_id, "RECONCILING", 60.0)

        update_job(
            job_id,
            status="RECONCILING",
            progress=80.0,
            extracted_data=json.dumps(finalized, default=str),
        )

        from app.core.storage import upload_file_to_s3

        upload_file_to_s3(
            file_bytes=json.dumps(
                {
                    "notes": run_notes,
                    "documents": [doc_id for doc_id, _ in doc_buckets],
                    "table_counts": {t: len(r) for t, r in finalized.items()},
                },
                indent=2,
            ).encode("utf-8"),
            object_key=f"extracted/{job_id}/reconciliation_notes.json",
            content_type="application/json",
        )

        publish_status(job_id, "RECONCILING", 100.0)
        logger.info(
            f"Job {job_id}: rebuilt from {len(doc_buckets)} document(s), "
            f"counts={{ {', '.join(f'{t}={len(r)}' for t, r in finalized.items())} }}"
        )

        from app.worker.tasks.translate import translate_and_provision

        translate_and_provision.apply_async(args=[job_id])

    except Exception as exc:
        logger.exception(f"Job {job_id}: rebuild failed")
        publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
        update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise


def _load_document_buckets(job_id: str) -> list[tuple[str, dict]]:
    """(document_id, buckets) for every EXTRACTED document, oldest first.

    Falls back to the legacy job-level S3 buckets for backfilled documents
    that predate per-document storage; the adopted buckets are persisted on
    the document so the fallback runs at most once.
    """
    docs = list_job_documents(job_id, "id", "status", "extracted_buckets")
    result: list[tuple[str, dict]] = []
    for doc in docs:
        if doc["status"] != "EXTRACTED":
            continue
        buckets = doc["extracted_buckets"]
        if isinstance(buckets, str):
            buckets = json.loads(buckets)
        if not buckets:
            buckets = _adopt_legacy_buckets(job_id, doc["id"])
        if buckets:
            result.append((doc["id"], buckets))
    return result


def _adopt_legacy_buckets(job_id: str, document_id: str) -> dict | None:
    from botocore.exceptions import ClientError

    from app.core.storage import get_s3_client

    s3 = get_s3_client()
    key = f"extracted/{job_id}/merged_buckets.json"
    try:
        response = s3.get_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError as exc:
        # Only a genuinely missing legacy file may be skipped; transient S3
        # errors must fail loud or the rebuild silently shrinks the dataset.
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code not in {"NoSuchKey", "404"}:
            raise
        logger.warning(
            f"Job {job_id}: document {document_id} has no buckets and no legacy "
            f"{key} — it cannot contribute to rebuilds"
        )
        return None
    buckets = json.loads(response["Body"].read().decode("utf-8"))
    update_document(document_id, extracted_buckets=json.dumps(buckets))
    logger.info(f"Job {job_id}: adopted legacy buckets for document {document_id}")
    return buckets
