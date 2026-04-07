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
from app.worker.celery_app import celery_app
from app.worker.db import get_job_field, publish_status, update_job

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
    table_name: str,
    chunk_index: int,
    chunk_text: str,
    pages: list[int],
    table_def_json: dict,
    link_targets_json: list[dict],
):
    """Extract rows for a single table from a single chunk.

    Returns a dict tagged with `table_name` so the merge step can bucket it.
    """
    from app.providers.factory import get_llm_provider

    table = TableDef.model_validate(table_def_json)
    link_targets = [RelationshipDef.model_validate(r) for r in link_targets_json]

    llm = get_llm_provider()
    response = llm.extract_table(chunk_text, table, link_targets)
    rows = response.data.get("rows", []) if isinstance(response.data, dict) else []

    logger.info(
        f"Job {job_id} table={table_name} chunk={chunk_index}: "
        f"extracted {len(rows)} rows, "
        f"tokens={response.usage.get('total_tokens', 0)}"
    )

    return {
        "table_name": table_name,
        "chunk_index": chunk_index,
        "rows": rows,
        "pages": pages,
        "tokens": response.usage,
    }


@celery_app.task(
    name="app.worker.tasks.extract.run_extraction",
    bind=True,
    queue="extraction",
)
def run_extraction(self, job_id: str):
    """Orchestrates the per-table Map phase.

    1. Load locked_model + job_type + (optional) target_chunks + section_map.
    2. For each TableDef in the locked model, build per-chunk extract tasks.
    3. Flatten into a single chord and call merge_results once everything completes.
    """
    try:
        publish_status(job_id, "EXTRACTING", 0.0)
        update_job(job_id, status="EXTRACTING", progress=0.0)

        # 1. Load context.
        job = get_job_field(
            job_id, "locked_model", "job_type", "target_chunks", "section_map"
        )
        locked_model_raw = _coerce_json(job["locked_model"])
        if not locked_model_raw:
            raise ValueError("locked_model is empty — cannot extract")
        locked_model = DatabaseModel.model_validate(locked_model_raw)

        job_type = job["job_type"]
        target_chunks_raw = _coerce_json(job["target_chunks"])
        section_map_raw = _coerce_json(job["section_map"]) or []
        sections = [SectionCandidate.model_validate(s) for s in section_map_raw]

        # 2. Build the source text per mode.
        from app.services.extraction import chunk_text

        if job_type == "TARGETED" and target_chunks_raw:
            base_chunks = [
                {
                    "chunk_index": i,
                    "text": chunk["text"],
                    "start_char": 0,
                    "end_char": len(chunk["text"]),
                    "pages": [chunk.get("page_number")] if chunk.get("page_number") else [],
                }
                for i, chunk in enumerate(target_chunks_raw)
            ]
            logger.info(
                f"Job {job_id}: TARGETED mode — {len(base_chunks)} retrieved chunks"
            )
        else:
            from app.core.storage import get_s3_client

            s3 = get_s3_client()
            response = s3.get_object(
                Bucket=settings.s3_bucket, Key=f"parsed/{job_id}/full_text.txt"
            )
            full_text = response["Body"].read().decode("utf-8")
            base_chunks = chunk_text(full_text, chunk_size=3000, overlap=500)
            logger.info(
                f"Job {job_id}: FULL mode — {len(base_chunks)} chunks from full text"
            )

        publish_status(job_id, "EXTRACTING", 10.0)

        # 3. For each table, decide which chunks feed it. Tables can be
        #    routed via section_map; otherwise they see every chunk.
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

            table_chunks = _filter_chunks_by_pages(base_chunks, allowed_pages)
            if not table_chunks:
                logger.warning(
                    f"Job {job_id} table={table.table_name}: no chunks matched section routing"
                )

            for ch in table_chunks:
                signatures.append(
                    extract_table_chunk.s(
                        job_id,
                        table.table_name,
                        ch["chunk_index"],
                        ch["text"],
                        ch.get("pages", []),
                        table_def_json,
                        link_targets_json,
                    )
                )

        if not signatures:
            raise ValueError(
                "no extraction tasks scheduled — locked_model has no tables or chunks"
            )

        from app.worker.tasks.merge import merge_results

        chord(group(*signatures))(merge_results.s(job_id))
        logger.info(
            f"Job {job_id}: extraction chord dispatched with {len(signatures)} chunk tasks "
            f"across {len(locked_model.tables)} tables"
        )

    except Exception as exc:
        logger.exception(f"Job {job_id}: extraction orchestration failed")
        publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        update_job(job_id, status="FAILED", error_message=str(exc))
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


def _allowed_pages_for_table(
    table_name: str, sections: list[SectionCandidate]
) -> set[int] | None:
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


def _filter_chunks_by_pages(
    chunks: list[dict[str, Any]], allowed_pages: set[int] | None
) -> list[dict[str, Any]]:
    """Keep only chunks whose pages overlap `allowed_pages`."""
    if allowed_pages is None:
        return chunks
    if not allowed_pages:
        return []
    return [
        ch for ch in chunks if any(p in allowed_pages for p in (ch.get("pages") or []))
    ]
