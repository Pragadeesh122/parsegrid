"""ParseGrid — Relational merge tasks (Phase 7 Reduce phase / Dataset Consolidation).

Receives the per-table extraction results from the chord and buckets
them by document and table. Each document's buckets are persisted on its
`extracted_buckets` column. For a creation run, `rebuild_dataset` then
reconciles the union of every document's buckets. For an append run, the
compatibility gate decides whether to rebuild immediately or pause for
human review.

NO LLM is used in merge or reconciliation.
"""

from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.services.safe_errors import public_error_message
from app.worker.celery_app import celery_app
from app.worker.db import get_job_field, publish_status, update_document, update_job

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.merge.merge_results",
    bind=True,
    queue="merge",
)
def merge_results(
    self, chunk_results: list[dict], job_id: str, append_document_id: str | None = None
):
    """Bucket chunk results per document, persist each document's buckets,
    then either run the append compatibility gate or rebuild the dataset.

    Each chunk result is
    `{document_id, table_name, chunk_key, rows, pages, tokens}`. Buckets are
    stored on the Document row in the shape
    `{"tables": {tbl: {"rows": [...], "chunk_pages": {chunk_key: [pages]}}}}`.
    """
    try:
        append = append_document_id is not None
        status = "APPENDING" if append else "MERGING"
        publish_status(job_id, status, 0.0, document_id=append_document_id)
        if not append:
            update_job(job_id, status="MERGING", progress=0.0)

        per_doc: dict[str, dict] = {}
        for entry in chunk_results:
            if not isinstance(entry, dict):
                continue
            doc_id = entry.get("document_id")
            table_name = entry.get("table_name")
            chunk_key = entry.get("chunk_key")
            rows = entry.get("rows") or []
            pages = entry.get("pages") or []
            if not doc_id or not table_name:
                continue
            tables = per_doc.setdefault(doc_id, {"tables": {}})["tables"]
            bucket = tables.setdefault(table_name, {"rows": [], "chunk_pages": {}})
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row = dict(row)
                row["__chunk_index"] = chunk_key
                bucket["rows"].append(row)
            if chunk_key is not None:
                bucket["chunk_pages"][chunk_key] = list(pages)

        for doc_id, payload in per_doc.items():
            update_document(
                doc_id,
                status="EXTRACTED",
                extracted_buckets=json.dumps(payload, default=str),
            )

        total_rows = sum(
            len(b["rows"]) for payload in per_doc.values() for b in payload["tables"].values()
        )
        logger.info(f"Job {job_id}: merged {total_rows} rows across {len(per_doc)} document(s)")

        if append:
            _run_compat_gate(job_id, append_document_id, per_doc.get(append_document_id))
            return

        publish_status(job_id, "MERGING", 100.0)
        from app.worker.tasks.reconcile import rebuild_dataset

        rebuild_dataset.apply_async(args=[job_id])

    except Exception as exc:
        logger.exception(f"Job {job_id}: merge failed")
        publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
        update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise


def _run_compat_gate(job_id: str, document_id: str, payload: dict | None) -> None:
    """Score the appended document against the locked model and route."""
    from app.schemas.extraction_model import DatabaseModel
    from app.services.consolidation import build_compat_report

    job = get_job_field(job_id, "locked_model", "document_profile")
    locked_raw = job["locked_model"]
    if isinstance(locked_raw, str):
        locked_raw = json.loads(locked_raw)
    locked_model = DatabaseModel.model_validate(locked_raw)

    profile_raw = job["document_profile"]
    if isinstance(profile_raw, str):
        profile_raw = json.loads(profile_raw)
    dataset_histogram = (profile_raw or {}).get("region_summary")

    tables = (payload or {}).get("tables") or {}
    buckets = {t: b.get("rows") or [] for t, b in tables.items()}
    report = build_compat_report(
        buckets,
        locked_model,
        max_pk_null_ratio=settings.append_max_pk_null_ratio,
        dataset_histogram=dataset_histogram,
        document_histogram=_document_histogram(job_id, document_id),
    )
    update_document(document_id, compat_report=json.dumps(report))

    if report["needs_review"]:
        update_job(job_id, status="AWAITING_APPEND_REVIEW", progress=50.0)
        publish_status(job_id, "AWAITING_APPEND_REVIEW", 50.0, document_id=document_id)
        logger.info(f"Job {job_id}: append {document_id} paused for review: {report['reasons']}")
        return

    from app.worker.tasks.reconcile import rebuild_dataset

    rebuild_dataset.apply_async(args=[job_id])
    logger.info(f"Job {job_id}: append {document_id} compatible — rebuilding")


def _document_histogram(job_id: str, document_id: str) -> dict[str, int] | None:
    """Region-type histogram of the appended document (flag-only drift layer).

    Best-effort: any S3/parse failure returns None — drift recording must
    never break the gate.
    """
    try:
        from collections import Counter

        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        response = s3.get_object(
            Bucket=settings.s3_bucket,
            Key=f"parsed/{job_id}/{document_id}/ocr_result.json",
        )
        ocr_json = json.loads(response["Body"].read().decode("utf-8"))
        histogram: Counter[str] = Counter()
        for page in ocr_json.get("pages") or []:
            for region in page.get("regions") or []:
                histogram[region.get("region_type") or "unknown"] += 1
        return dict(histogram)
    except Exception:
        logger.warning(
            f"Job {job_id}: could not build drift histogram for {document_id}",
            exc_info=True,
        )
        return None
