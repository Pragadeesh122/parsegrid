"""ParseGrid — LLM extraction tasks (Map phase).

Uses OpenAI via BaseLLMProvider with Structured Outputs (strict: true).
Orchestrates parallel chunk extraction using Celery groups and chords.
"""

import json
import logging

from celery import chord, group

from app.core.config import settings
from app.worker.celery_app import celery_app
from app.worker.db import get_job_field, publish_status, update_job

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.extract.extract_chunk",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
    queue="extraction",
)
def extract_chunk(self, job_id: str, chunk_index: int, chunk_text: str, schema: dict):
    """Extract structured data from a single chunk using the locked schema.

    Uses the configured LLM provider with structured output enforcement.
    Returns chunk result for the merge phase.

    Decorated with autoretry_for + retry_backoff to handle OpenAI rate limits
    at the individual chunk level rather than failing the entire job.
    """
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
    4. Fan out extract_chunk tasks via Celery chord (parallel, NOT sequential)
    5. Chord callback triggers merge_results when all chunks complete
    """
    try:
        publish_status(job_id, "EXTRACTING", 0.0)
        update_job(job_id, status="EXTRACTING", progress=0.0)

        # 1. Load locked schema and job_type from DB
        job = get_job_field(job_id, "locked_schema", "job_type", "target_chunks")
        locked_schema = job["locked_schema"]
        if isinstance(locked_schema, str):
            locked_schema = json.loads(locked_schema)

        job_type = job["job_type"]
        target_chunks_raw = job["target_chunks"]

        # 2. Build chunks based on job_type
        if job_type == "TARGETED" and target_chunks_raw:
            # Targeted mode: use only the retrieved RAG chunks
            if isinstance(target_chunks_raw, str):
                target_chunks_raw = json.loads(target_chunks_raw)

            chunks = [
                {
                    "chunk_index": i,
                    "text": chunk["text"],
                    "start_char": 0,
                    "end_char": len(chunk["text"]),
                }
                for i, chunk in enumerate(target_chunks_raw)
            ]
            logger.info(f"Job {job_id}: TARGETED mode — {len(chunks)} retrieved chunks")

        else:
            # Full mode: load entire document from S3 and chunk
            from app.core.storage import get_s3_client

            s3 = get_s3_client()
            parsed_key = f"parsed/{job_id}/full_text.txt"
            response = s3.get_object(Bucket=settings.s3_bucket, Key=parsed_key)
            full_text = response["Body"].read().decode("utf-8")

            from app.services.extraction import chunk_text

            chunks = chunk_text(full_text, chunk_size=3000, overlap=500)
            logger.info(f"Job {job_id}: FULL mode — {len(chunks)} chunks created")

        logger.info(f"Job {job_id}: starting parallel extraction")

        publish_status(job_id, "EXTRACTING", 10.0)

        # 4. Fan out extraction tasks via Celery chord (parallel execution)
        from app.worker.tasks.merge import merge_results

        extraction_tasks = group(
            extract_chunk.s(job_id, chunk["chunk_index"], chunk["text"], locked_schema)
            for chunk in chunks
        )

        # Chord: parallel extraction → merge callback
        chord(extraction_tasks)(
            merge_results.s(job_id, locked_schema)
        )

        logger.info(f"Job {job_id}: extraction chord dispatched with {len(chunks)} tasks")

    except Exception as exc:
        logger.exception(f"Job {job_id}: extraction orchestration failed")
        publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        update_job(job_id, status="FAILED", error_message=str(exc))
        raise
