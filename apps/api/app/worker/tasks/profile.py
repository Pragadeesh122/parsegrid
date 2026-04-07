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
from app.worker.celery_app import celery_app
from app.worker.db import get_job_field, publish_status, update_job

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

        # 1. Load the OCR JSON from S3.
        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        ocr_key = f"parsed/{job_id}/ocr_result.json"
        response = s3.get_object(Bucket=settings.s3_bucket, Key=ocr_key)
        ocr_json = json.loads(response["Body"].read().decode("utf-8"))

        publish_status(job_id, "PROFILING", 20.0)

        # 2. Sample pages + region histogram.
        from app.services.profiling import build_profile_context, profile_document

        sampled_pages, region_summary = profile_document(ocr_json)
        context_text = build_profile_context(sampled_pages, ocr_json)
        total_pages = ocr_json.get("page_count") or 0

        logger.info(
            f"Job {job_id}: profiling sampled {len(sampled_pages)}/{total_pages} pages, "
            f"regions={region_summary}"
        )

        publish_status(job_id, "PROFILING", 50.0)

        # 3. LLM proposes the DatabaseModel. The first call has no profile —
        #    we use the LLM's response to derive sections and the
        #    recommended_extraction_type, then build the DocumentProfile.
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
            sampled_pages=sampled_pages,
            region_summary=region_summary,
            sections=[],  # MVP: profiling does not produce sections; review UI handles routing
            recommended_extraction_type=proposed_model.extraction_type,
            rationale=(
                f"Sampled {len(sampled_pages)} pages out of {total_pages}. "
                f"LLM proposed {len(proposed_model.tables)} table(s) "
                f"with {len(proposed_model.relationships)} relationship(s)."
            ),
        )

        # 4. Persist and transition.
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
        publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        update_job(job_id, status="FAILED", error_message=str(exc))
        raise
