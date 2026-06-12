"""ParseGrid — Per-table LLM extraction tasks (Phase 7 Map phase).

For each table in the locked DatabaseModel:
1. Pick the source text (full doc for FULL, retrieved chunks for TARGETED).
2. If the table has section routing, restrict the source text to the
   pages assigned to it. Otherwise use everything.
3. Chunk the text and emit one extract_table_chunk Celery task per
   (table, chunk) pair.

All chunk tasks are flattened into a single Celery group, and a chord
callback dispatches `merge_results` once they all complete.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from celery import chord, group

from app.core.config import settings
from app.schemas.extraction_model import (
    DatabaseModel,
    RelationshipDef,
    SectionCandidate,
    TableDef,
)
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
    name="app.worker.tasks.extract.extract_table_chunk",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
    queue="extraction",
)
def extract_table_chunk(
    self,
    job_id: str,
    document_id: str,
    table_name: str,
    chunk_key: str,
    chunk_text: str,
    pages: list[int],
    table_def_json: dict,
    link_targets_json: list[dict],
):
    """Extract rows for a single table from a single chunk.

    Returns a dict tagged with `document_id` and `table_name` so the merge
    step can bucket it per document and per table.
    """
    from app.providers.factory import get_llm_provider

    table = TableDef.model_validate(table_def_json)
    link_targets = [RelationshipDef.model_validate(r) for r in link_targets_json]

    llm = get_llm_provider()
    response = llm.extract_table(chunk_text, table, link_targets)
    rows = response.data.get("rows", []) if isinstance(response.data, dict) else []

    logger.info(
        f"Job {job_id} table={table_name} chunk={chunk_key}: "
        f"extracted {len(rows)} rows, "
        f"tokens={response.usage.get('total_tokens', 0)}"
    )

    return {
        "document_id": document_id,
        "table_name": table_name,
        "chunk_key": chunk_key,
        "rows": rows,
        "pages": pages,
        "tokens": response.usage,
    }


@celery_app.task(
    name="app.worker.tasks.extract.run_extraction",
    bind=True,
    queue="extraction",
)
def run_extraction(self, job_id: str, document_id: str | None = None):
    """Orchestrate the per-table Map phase across the job's documents.

    document_id=None  → creation: extract every OCR_DONE document.
    document_id set   → append: extract just that document; merge runs the
                        compatibility gate before rebuilding.
    """
    try:
        append = document_id is not None
        status = "APPENDING" if append else "EXTRACTING"
        publish_status(job_id, status, 0.0, document_id=document_id)
        update_job(job_id, status=status, progress=0.0)

        job = get_job_field(job_id, "locked_model", "job_type", "target_chunks", "section_map")
        locked_model_raw = _coerce_json(job["locked_model"])
        if not locked_model_raw:
            raise ValueError("locked_model is empty — cannot extract")
        locked_model = DatabaseModel.model_validate(locked_model_raw)

        job_type = job["job_type"]
        target_chunks_raw = _coerce_json(job["target_chunks"])
        section_map_raw = _coerce_json(job["section_map"]) or []
        sections = [SectionCandidate.model_validate(s) for s in section_map_raw]

        if append:
            documents = [{"id": document_id}]
        else:
            documents = [
                d for d in list_job_documents(job_id, "id", "status") if d["status"] == "OCR_DONE"
            ]
        if not documents:
            raise ValueError("no documents ready for extraction")

        from app.services.extraction import chunk_text

        # Build per-document chunks, keys prefixed with the document id.
        doc_chunks: list[tuple[str, dict]] = []  # (document_id, chunk)
        if job_type == "TARGETED" and target_chunks_raw:
            doc_id = documents[0]["id"]
            for i, chunk in enumerate(target_chunks_raw):
                doc_chunks.append(
                    (
                        doc_id,
                        {
                            "chunk_key": f"{doc_id}:{i}",
                            "text": chunk["text"],
                            "pages": [chunk.get("page_number")] if chunk.get("page_number") else [],
                        },
                    )
                )
            logger.info(f"Job {job_id}: TARGETED mode — {len(doc_chunks)} retrieved chunks")
        else:
            from app.core.storage import get_s3_client

            s3 = get_s3_client()
            for d in documents:
                response = s3.get_object(
                    Bucket=settings.s3_bucket,
                    Key=f"parsed/{job_id}/{d['id']}/full_text.txt",
                )
                full_text = response["Body"].read().decode("utf-8")
                for ch in chunk_text(full_text, chunk_size=3000, overlap=500):
                    doc_chunks.append(
                        (
                            d["id"],
                            {
                                "chunk_key": f"{d['id']}:{ch['chunk_index']}",
                                "text": ch["text"],
                                "pages": ch.get("pages", []),
                            },
                        )
                    )
            logger.info(
                f"Job {job_id}: FULL mode — {len(doc_chunks)} chunks across "
                f"{len(documents)} document(s)"
            )

        for d in documents:
            update_document(d["id"], status="EXTRACTING")

        publish_status(job_id, status, 10.0, document_id=document_id)

        signatures = []
        for table in locked_model.tables:
            allowed_pages = _allowed_pages_for_table(table.table_name, sections)
            link_targets = [
                rel
                for rel in locked_model.relationships
                if rel.source_table == table.table_name and rel.enabled
            ]
            link_targets_json = [r.model_dump() for r in link_targets]
            table_def_json = table.model_dump()

            for doc_id, ch in doc_chunks:
                if allowed_pages is not None and not _chunk_in_pages(ch, allowed_pages):
                    continue
                signatures.append(
                    extract_table_chunk.s(
                        job_id,
                        doc_id,
                        table.table_name,
                        ch["chunk_key"],
                        ch["text"],
                        ch.get("pages", []),
                        table_def_json,
                        link_targets_json,
                    )
                )

        if not signatures:
            raise ValueError("no extraction tasks scheduled — locked_model has no tables or chunks")

        from app.worker.tasks.merge import merge_results

        chord(group(*signatures))(merge_results.s(job_id, document_id))
        logger.info(
            f"Job {job_id}: extraction chord dispatched with {len(signatures)} chunk tasks "
            f"across {len(locked_model.tables)} tables"
        )

    except Exception as exc:
        logger.exception(f"Job {job_id}: extraction orchestration failed")
        if document_id is not None:
            update_document(document_id, status="FAILED", error_message=public_error_message(exc))
            update_job(job_id, status="COMPLETED", progress=100.0)
            publish_status(
                job_id,
                "COMPLETED",
                100.0,
                error_message=public_error_message(exc),
                document_id=document_id,
            )
        else:
            publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
            update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_json(value: Any) -> Any:
    """Convert a possibly-stringified JSON column value to its Python form."""
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def _allowed_pages_for_table(table_name: str, sections: list[SectionCandidate]) -> set[int] | None:
    """Compute the page set this table should see.

    Returns None when there is no section routing (table sees every chunk).
    Returns an empty set when sections exist but none are assigned to this
    table — the caller will then route zero chunks to this table, which is
    the front-matter-pollution-blocking behavior we want.
    """
    if not sections:
        return None

    pages: set[int] = set()
    saw_assignment = False
    for section in sections:
        if table_name in section.assigned_tables:
            saw_assignment = True
            start, end = section.page_range
            for p in range(start, end + 1):
                pages.add(p)
    if not saw_assignment:
        return None  # No routing decisions for this table — show everything.
    return pages


def _chunk_in_pages(chunk: dict[str, Any], allowed_pages: set[int]) -> bool:
    """True when the chunk's pages overlap `allowed_pages` (empty set → False)."""
    if not allowed_pages:
        return False
    return any(p in allowed_pages for p in (chunk.get("pages") or []))
