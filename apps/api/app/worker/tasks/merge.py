"""ParseGrid — Relational merge tasks (Phase 7 Reduce phase).

Receives the per-table extraction results from the chord and buckets
them by `table_name`. Applies fingerprint-based dedupe within each
bucket, then dispatches `reconcile_and_translate` for the remaining
deterministic stages (normalization, FK resolution, provisioning).

NO LLM is used in merge or reconciliation.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from app.worker.celery_app import celery_app
from app.worker.db import publish_status, update_job

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.merge.merge_results",
    bind=True,
    queue="merge",
)
def merge_results(self, chunk_results: list[dict], job_id: str):
    """Bucket chunk results by table and dispatch reconciliation.

    Each chunk result is `{table_name, chunk_index, rows, pages, tokens}`.
    The output payload is `{table_name: {rows: [...], chunk_pages: {...}}}`
    so reconciliation can attach provenance per row.
    """
    try:
        publish_status(job_id, "MERGING", 0.0)
        update_job(job_id, status="MERGING", progress=0.0)

        bucket_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        bucket_pages: dict[str, dict[int, list[int]]] = defaultdict(dict)

        for entry in chunk_results:
            if not isinstance(entry, dict):
                continue
            table_name = entry.get("table_name")
            chunk_index = entry.get("chunk_index")
            rows = entry.get("rows") or []
            pages = entry.get("pages") or []
            if not table_name:
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row = dict(row)
                row["__chunk_index"] = chunk_index
                bucket_rows[table_name].append(row)
            if chunk_index is not None:
                bucket_pages[table_name][chunk_index] = list(pages)

        publish_status(job_id, "MERGING", 50.0)

        # Persist intermediate buckets to S3 for debugging.
        from app.core.storage import upload_file_to_s3

        merged_payload = {
            "tables": {
                tbl: {
                    "rows": rows,
                    "chunk_pages": bucket_pages.get(tbl, {}),
                }
                for tbl, rows in bucket_rows.items()
            }
        }
        upload_file_to_s3(
            file_bytes=json.dumps(merged_payload, indent=2, default=str).encode("utf-8"),
            object_key=f"extracted/{job_id}/merged_buckets.json",
            content_type="application/json",
        )

        update_job(job_id, status="MERGING", progress=80.0)
        publish_status(job_id, "MERGING", 100.0)

        logger.info(
            f"Job {job_id}: merged {sum(len(r) for r in bucket_rows.values())} rows "
            f"across {len(bucket_rows)} tables"
        )

        from app.worker.tasks.reconcile import reconcile_and_translate

        reconcile_and_translate.apply_async(
            args=[job_id, dict(bucket_rows), {k: dict(v) for k, v in bucket_pages.items()}]
        )

    except Exception as exc:
        logger.exception(f"Job {job_id}: merge failed")
        publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        update_job(job_id, status="FAILED", error_message=str(exc))
        raise
