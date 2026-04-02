"""ParseGrid — LLM extraction tasks (Map phase).

Uses OpenAI via BaseLLMProvider with Structured Outputs (strict: true).
Orchestrates parallel chunk extraction using Celery groups and chords.
"""

import json
import logging

import redis
from celery import chord, group

from app.core.config import settings
from app.worker.celery_app import celery_app
from app.worker.tasks.ocr import _publish_status, _update_job_in_db

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.extract.extract_chunk",
    bind=True,
    max_retries=3,
    queue="extraction",
)
def extract_chunk(self, job_id: str, chunk_index: int, chunk_text: str, schema: dict):
    """Extract structured data from a single chunk using the locked schema.

    Uses the configured LLM provider with structured output enforcement.
    Returns chunk result for the merge phase.
    """
    try:
        from app.providers.factory import get_llm_provider

        llm = get_llm_provider()
        result = llm.extract_structured(chunk_text, schema)

        logger.info(
            f"Job {job_id} chunk {chunk_index}: "
            f"extracted {len(result.data.get('items', []) if isinstance(result.data, dict) else result.data)} records, "
            f"tokens used: {result.usage.get('total_tokens', 0)}"
        )

        return {
            "chunk_index": chunk_index,
            "data": result.data,
            "tokens": result.usage,
        }

    except Exception as exc:
        logger.exception(f"Job {job_id} chunk {chunk_index}: extraction failed")
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(
    name="app.worker.tasks.extract.run_extraction",
    bind=True,
    queue="extraction",
)
def run_extraction(self, job_id: str):
    """Orchestrates the Map phase: chunks the document and fans out extraction.

    1. Load parsed text from S3
    2. Load locked schema from job record
    3. Chunk text into overlapping blocks
    4. Fan out extract_chunk tasks via Celery group
    5. Use chord to trigger merge_results when all chunks complete
    """
    try:
        _publish_status(job_id, "EXTRACTING", 0.0)
        _update_job_in_db(job_id, status="EXTRACTING", progress=0.0)

        # 1. Load parsed text from S3
        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        parsed_key = f"parsed/{job_id}/full_text.txt"
        response = s3.get_object(Bucket=settings.s3_bucket, Key=parsed_key)
        full_text = response["Body"].read().decode("utf-8")

        # 2. Load locked schema from DB
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session

        sync_url = settings.database_url.replace("+asyncpg", "+psycopg2").replace(
            "postgresql+psycopg2", "postgresql"
        )
        engine = create_engine(sync_url)
        with Session(engine) as session:
            row = session.execute(
                text("SELECT locked_schema FROM jobs WHERE id = :job_id"),
                {"job_id": job_id},
            ).one()
            locked_schema = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        engine.dispose()

        # 3. Chunk text
        from app.services.extraction import chunk_text

        chunks = chunk_text(full_text, chunk_size=3000, overlap=500)

        logger.info(f"Job {job_id}: {len(chunks)} chunks created, starting parallel extraction")

        _publish_status(job_id, "EXTRACTING", 10.0)

        # 4. Fan out extraction tasks via Celery chord
        # The chord will run all extract_chunk tasks in parallel,
        # then trigger merge_results with all the results
        from app.worker.tasks.merge import merge_results

        extraction_tasks = group(
            extract_chunk.s(job_id, chunk["chunk_index"], chunk["text"], locked_schema)
            for chunk in chunks
        )

        # Chord: parallel extraction → merge callback
        workflow = chord(extraction_tasks)(
            merge_results.s(job_id, locked_schema)
        )

        logger.info(f"Job {job_id}: extraction chord dispatched with {len(chunks)} tasks")

    except Exception as exc:
        logger.exception(f"Job {job_id}: extraction orchestration failed")
        _publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        _update_job_in_db(job_id, status="FAILED", error_message=str(exc))
        raise
