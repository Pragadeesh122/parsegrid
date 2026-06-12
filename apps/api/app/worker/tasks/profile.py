"""ParseGrid — Profiling task (Phase 7).

For FULL jobs, after OCR completes this task:

1. Loads the OCR JSON from S3
2. Picks representative pages and builds a region-type histogram
3. Calls the LLM to propose a `DatabaseModel`
4. Stores `document_profile` and `proposed_model` and transitions the job
   to `MODEL_PROPOSED` for human review
"""

import json
import logging

from app.core.config import settings
from app.schemas.extraction_model import DocumentProfile
from app.services.safe_errors import public_error_message
from app.worker.celery_app import celery_app
from app.worker.db import list_job_documents, publish_status, update_job

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.profile.profile_and_propose",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
    queue="ocr",
)
def profile_and_propose(self, job_id: str):
    """Run whole-document profiling and propose a DatabaseModel."""
    try:
        publish_status(job_id, "PROFILING", 0.0)
        update_job(job_id, status="PROFILING", progress=0.0)

        # 1. Load every OCR-complete document's OCR JSON from S3.
        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        documents = [
            d
            for d in list_job_documents(job_id, "id", "filename", "page_count", "status")
            if d["status"] in ("OCR_DONE", "EXTRACTED")
        ]
        if not documents:
            raise ValueError("no OCR-complete documents to profile")

        ocr_by_doc: dict[str, dict] = {}
        for d in documents:
            key = f"parsed/{job_id}/{d['id']}/ocr_result.json"
            response = s3.get_object(Bucket=settings.s3_bucket, Key=key)
            ocr_by_doc[d["id"]] = json.loads(response["Body"].read().decode("utf-8"))

        publish_status(job_id, "PROFILING", 20.0)

        # 2. Split the sampling budget and profile each document.
        from app.services.consolidation import allocate_sampling_budget
        from app.services.profiling import (
            MAX_SAMPLED_PAGES,
            build_profile_context,
            profile_document,
        )

        page_counts = {d["id"]: d["page_count"] or 0 for d in documents}
        budgets = allocate_sampling_budget(page_counts, budget=MAX_SAMPLED_PAGES)

        sampled_by_doc: dict[str, list[int]] = {}
        merged_histogram: dict[str, int] = {}
        context_blocks: list[str] = []
        filenames = {d["id"]: d["filename"] for d in documents}
        # Upload order (documents is ORDER BY created_at) — the user's first
        # file leads the LLM context.
        for doc_id in (d["id"] for d in documents):
            sampled, histogram = profile_document(ocr_by_doc[doc_id], budget=budgets.get(doc_id))
            sampled_by_doc[doc_id] = sampled
            for rtype, count in histogram.items():
                merged_histogram[rtype] = merged_histogram.get(rtype, 0) + count
            block = build_profile_context(sampled, ocr_by_doc[doc_id])
            context_blocks.append(f"=== Document: {filenames[doc_id]} ===\n{block}")
        context_text = "\n\n".join(context_blocks)
        total_pages = sum(page_counts.values())
        all_sampled = sorted({p for pages in sampled_by_doc.values() for p in pages})

        logger.info(
            f"Job {job_id}: profiled {len(documents)} document(s), "
            f"budgets={budgets}, regions={merged_histogram}"
        )

        publish_status(job_id, "PROFILING", 50.0)

        # 3. LLM proposes the DatabaseModel from the combined context.
        from app.providers.factory import get_llm_provider

        llm = get_llm_provider()
        proposed_model = llm.generate_model(
            document_text=context_text,
            profile=None,
            num_pages=total_pages,
        )

        publish_status(job_id, "PROFILING", 80.0)

        document_profile = DocumentProfile(
            total_pages=total_pages,
            sampled_pages=all_sampled,
            sampled_pages_by_document=sampled_by_doc,
            region_summary=merged_histogram,
            sections=[],  # MVP: profiling does not produce sections; review UI handles routing
            recommended_extraction_type=proposed_model.extraction_type,
            rationale=(
                f"Sampled {sum(len(v) for v in sampled_by_doc.values())} pages "
                f"across {len(documents)} document(s) ({total_pages} total pages). "
                f"LLM proposed {len(proposed_model.tables)} table(s) "
                f"with {len(proposed_model.relationships)} relationship(s)."
            ),
        )

        update_job(
            job_id,
            status="MODEL_PROPOSED",
            progress=100.0,
            document_profile=json.dumps(document_profile.model_dump()),
            proposed_model=json.dumps(proposed_model.model_dump()),
        )
        publish_status(job_id, "MODEL_PROPOSED", 100.0)

        logger.info(
            f"Job {job_id}: profiling complete, "
            f"extraction_type={proposed_model.extraction_type}, "
            f"tables={[t.table_name for t in proposed_model.tables]}"
        )

    except Exception as exc:
        logger.exception(f"Job {job_id}: profiling failed: {exc}")
        publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
        update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise
