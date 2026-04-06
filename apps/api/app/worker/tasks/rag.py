"""ParseGrid — RAG pipeline tasks.

Handles document indexing (chunking + embedding) for the targeted extraction pipeline.
"""

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.worker.celery_app import celery_app
from app.worker.db import get_sync_engine, publish_status, update_job

logger = logging.getLogger(__name__)

# Token-based chunking parameters (optimized for retrieval, not LLM context)
CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50
# Rough approximation: 1 token ≈ 4 characters
CHARS_PER_TOKEN = 4


def _chunk_text_by_tokens(
    full_text: str,
    chunk_size: int = CHUNK_SIZE_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
) -> list[dict]:
    """Split text into overlapping chunks based on approximate token count.

    Splits on sentence/line boundaries. Handles OCR output that uses single
    newlines between lines (not just double newlines between paragraphs).
    """
    chunk_size_chars = chunk_size * CHARS_PER_TOKEN
    overlap_chars = overlap * CHARS_PER_TOKEN

    # Split into lines — OCR text uses \n between lines
    lines = full_text.split("\n")

    chunks: list[dict] = []
    current_text = ""
    current_page = 1

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Track page number from page markers
        if stripped.startswith("--- Page ") and stripped.endswith("---"):
            try:
                current_page = int(stripped.split("Page ")[1].split(" ")[0].rstrip("-"))
            except (IndexError, ValueError):
                pass
            continue

        if len(current_text) + len(stripped) + 1 > chunk_size_chars and current_text:
            chunks.append({
                "chunk_index": len(chunks),
                "text": current_text.strip(),
                "page_number": current_page,
            })
            # Overlap: keep the tail of the current chunk
            if len(current_text) > overlap_chars:
                current_text = current_text[-overlap_chars:] + "\n" + stripped
            else:
                current_text = current_text + "\n" + stripped
        else:
            current_text += ("\n" if current_text else "") + stripped

    if current_text.strip():
        chunks.append({
            "chunk_index": len(chunks),
            "text": current_text.strip(),
            "page_number": current_page,
        })

    return chunks


@celery_app.task(
    name="app.worker.tasks.rag.index_document",
    bind=True,
    max_retries=3,
    queue="ocr",
)
def index_document(self, job_id: str):
    """Index a document into pgvector for targeted RAG extraction.

    1. Load full_text.txt from S3
    2. Chunk text (500 tokens, 50 overlap)
    3. Embed chunks via OpenAI text-embedding-3-small
    4. Bulk insert DocumentChunk rows into PostgreSQL
    5. Set job status to AWAITING_QUERY
    """
    try:
        publish_status(job_id, "INDEXING", 5.0)
        update_job(job_id, status="INDEXING", progress=5.0)

        # 1. Load parsed text from S3
        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        parsed_key = f"parsed/{job_id}/full_text.txt"
        response = s3.get_object(Bucket=settings.s3_bucket, Key=parsed_key)
        full_text = response["Body"].read().decode("utf-8")

        logger.info(f"Job {job_id}: loaded {len(full_text)} chars for indexing")
        publish_status(job_id, "INDEXING", 20.0)

        # 2. Chunk text
        chunks = _chunk_text_by_tokens(full_text)
        logger.info(f"Job {job_id}: split into {len(chunks)} chunks for embedding")
        publish_status(job_id, "INDEXING", 30.0)

        # 3. Embed all chunks
        from app.providers.factory import get_embedding_provider

        embedder = get_embedding_provider()
        chunk_texts = [c["text"] for c in chunks]
        embeddings = embedder.embed_texts(chunk_texts)

        logger.info(f"Job {job_id}: embedded {len(embeddings)} chunks ({embedder.dimension}d)")
        publish_status(job_id, "INDEXING", 70.0)

        # 4. Bulk insert into document_chunks
        engine = get_sync_engine()
        with Session(engine) as session:
            for chunk, embedding in zip(chunks, embeddings):
                chunk_id = str(uuid.uuid4())
                embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
                session.execute(
                    text(
                        "INSERT INTO document_chunks "
                        "(id, job_id, page_number, chunk_index, chunk_text, embedding) "
                        "VALUES (:id, :job_id, :page_number, :chunk_index, :chunk_text, :embedding)"
                    ),
                    {
                        "id": chunk_id,
                        "job_id": job_id,
                        "page_number": chunk["page_number"],
                        "chunk_index": chunk["chunk_index"],
                        "chunk_text": chunk["text"],
                        "embedding": embedding_str,
                    },
                )
            session.commit()

        logger.info(f"Job {job_id}: inserted {len(chunks)} document chunks")
        publish_status(job_id, "INDEXING", 90.0)

        # 5. Set status to AWAITING_QUERY
        update_job(job_id, status="AWAITING_QUERY", progress=100.0)
        publish_status(job_id, "AWAITING_QUERY", 100.0)

        logger.info(f"Job {job_id}: indexing complete, awaiting user query")

    except Exception as exc:
        logger.exception(f"Job {job_id}: indexing failed: {exc}")
        publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        update_job(job_id, status="FAILED", error_message=str(exc))
        raise self.retry(exc=exc, countdown=60)
