"""ParseGrid — OCR processing tasks.

Uses PaddleOCR (local, air-gapped) via the BaseOCRProvider interface.
After OCR, triggers the Schema Generator Agent via the BaseLLMProvider.
"""

import json
import logging
import os
import tempfile

import redis

from app.core.config import settings
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def _publish_status(job_id: str, status: str, progress: float, **extra):
    """Publish a status update to Redis PubSub for SSE streaming."""
    r = redis.from_url(settings.redis_url)
    channel = f"job:{job_id}:status"
    data = {"status": status, "progress": progress, **extra}
    r.publish(channel, json.dumps(data))
    r.close()


def _update_job_in_db(job_id: str, **fields):
    """Synchronously update job fields in the metadata database.

    Uses a sync SQLAlchemy session since Celery workers are sync.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    sync_url = settings.database_url.replace("+asyncpg", "+psycopg2").replace(
        "postgresql+psycopg2", "postgresql"
    )
    engine = create_engine(sync_url)

    set_clauses = ", ".join(f"{k} = :{k}" for k in fields)
    with Session(engine) as session:
        session.execute(
            text(f"UPDATE jobs SET {set_clauses}, updated_at = NOW() WHERE id = :job_id"),
            {"job_id": job_id, **fields},
        )
        session.commit()
    engine.dispose()


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
        _publish_status(job_id, "OCR_PROCESSING", 5.0)
        _update_job_in_db(job_id, status="OCR_PROCESSING", progress=5.0)

        # 1. Get job details from DB
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session

        sync_url = settings.database_url.replace("+asyncpg", "+psycopg2").replace(
            "postgresql+psycopg2", "postgresql"
        )
        engine = create_engine(sync_url)
        with Session(engine) as session:
            row = session.execute(
                text("SELECT file_key, filename FROM jobs WHERE id = :job_id"),
                {"job_id": job_id},
            ).one()
            file_key = row[0]
            filename = row[1]
        engine.dispose()

        _publish_status(job_id, "OCR_PROCESSING", 10.0)

        # 2. Download file from S3 to temp directory
        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, filename)
            s3.download_file(settings.s3_bucket, file_key, local_path)
            logger.info(f"Downloaded {file_key} → {local_path}")

            _publish_status(job_id, "OCR_PROCESSING", 20.0)

            # 3. Run PaddleOCR with layout analysis
            from app.providers.factory import get_ocr_provider

            ocr = get_ocr_provider()
            ocr_result = ocr.process_document(local_path)

            _publish_status(job_id, "OCR_PROCESSING", 60.0)
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

            _publish_status(job_id, "OCR_PROCESSING", 70.0)

            # 5. Generate schema proposal using LLM
            from app.providers.factory import get_llm_provider

            llm = get_llm_provider()

            # Sample the first few pages for schema discovery
            sample_text = "\n\n".join(
                p.full_text for p in ocr_result.pages[:5]
            )
            proposed_schema = llm.generate_schema(sample_text, ocr_result.page_count)

            _publish_status(job_id, "SCHEMA_PROPOSED", 90.0)

            # 6. Update job with results
            _update_job_in_db(
                job_id,
                status="SCHEMA_PROPOSED",
                progress=100.0,
                page_count=ocr_result.page_count,
                proposed_schema=json.dumps(proposed_schema),
            )

            _publish_status(job_id, "SCHEMA_PROPOSED", 100.0)

        logger.info(f"Job {job_id}: OCR + schema proposal complete")

    except Exception as exc:
        logger.exception(f"Job {job_id}: OCR failed: {exc}")
        _publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        _update_job_in_db(
            job_id,
            status="FAILED",
            error_message=str(exc),
        )
        raise self.retry(exc=exc, countdown=60)
