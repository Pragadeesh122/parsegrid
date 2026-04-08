"""ParseGrid — Translation + provisioning task (Phase 7).

The DDL is built deterministically from the locked DatabaseModel — no LLM
is called here. The reconciled, multi-table data on the Job is handed to
the output provider for FK-ordered insertion.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.schemas.extraction_model import DatabaseModel
from app.worker.celery_app import celery_app
from app.worker.db import get_job_field, publish_status, update_job

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.translate.translate_and_provision",
    bind=True,
    queue="translation",
)
def translate_and_provision(self, job_id: str):
    """Build DDL deterministically from locked_model, then provision the schema."""
    try:
        publish_status(job_id, "TRANSLATING", 0.0)
        update_job(job_id, status="TRANSLATING", progress=0.0)

        # 1. Load locked_model + reconciled data + output format.
        job = get_job_field(job_id, "locked_model", "extracted_data", "output_format")
        locked_raw = _coerce_json(job["locked_model"])
        if not locked_raw:
            raise ValueError("locked_model missing — cannot translate")
        locked_model = DatabaseModel.model_validate(locked_raw)

        extracted_raw = _coerce_json(job["extracted_data"]) or {}
        if not isinstance(extracted_raw, dict):
            raise ValueError("extracted_data is not a dict[table_name, rows]")

        output_format = _coerce_output_format(job.get("output_format"))

        # 2. Build DDL.
        from app.services.ddl import build_ddl_with_notes

        schema_name = f"job_{job_id.replace('-', '_')}"
        ddl_statements, normalized_model, ddl_notes = build_ddl_with_notes(
            locked_model, schema_name
        )
        if ddl_notes:
            logger.info(f"Job {job_id}: DDL notes: {ddl_notes}")

        publish_status(job_id, "TRANSLATING", 40.0)

        # 3. Hand off to the output provider.
        publish_status(job_id, "PROVISIONING", 50.0)
        update_job(job_id, status="PROVISIONING", progress=50.0)

        from app.services.provisioning import provision_and_insert

        result = provision_and_insert(
            schema_name=schema_name,
            ddl_statements=ddl_statements,
            data=extracted_raw,
            model=normalized_model,
            output_format=output_format,
        )

        publish_status(job_id, "PROVISIONING", 90.0)

        # 4. Audit + completion.
        now = datetime.now(timezone.utc).isoformat()
        update_job(
            job_id,
            status="COMPLETED",
            progress=100.0,
            output_schema_name=result.schema_name,
            connection_string=result.connection_string,
            provisioned_rows=result.rows_inserted,
            provisioned_at=now,
            target_ddl=result.ddl_executed,
        )

        publish_status(
            job_id,
            "COMPLETED",
            100.0,
            connection_string=result.connection_string,
        )

        logger.info(
            f"Job {job_id}: COMPLETED — {result.rows_inserted} rows → {result.connection_string}"
        )

    except Exception as exc:
        logger.exception(f"Job {job_id}: translation/provisioning failed")
        publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        update_job(job_id, status="FAILED", error_message=str(exc))
        raise


def _coerce_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def _coerce_output_format(value: Any) -> str:
    if value is None:
        return "SQL"
    if hasattr(value, "value"):
        return str(value.value).upper()
    return str(value).upper()
