"""ParseGrid — Celery task lifecycle callbacks.

Hooks into Celery's signal system to catch hard failures (OOM, segfault,
hard timeout) that bypass a task's own except block. Updates the job
status to FAILED in PostgreSQL and publishes the failure to Redis PubSub.
"""

import logging

from celery.signals import task_failure

logger = logging.getLogger(__name__)

# Map task names → position of job_id in the task's argument list.
# Most tasks receive (job_id, ...) at index 0.
# merge_results is a chord callback: (chunk_results, job_id, schema) → index 1.
_JOB_ID_ARG_INDEX: dict[str, int] = {
    "app.worker.tasks.ocr.process_document": 0,
    "app.worker.tasks.extract.run_extraction": 0,
    "app.worker.tasks.extract.extract_chunk": 0,
    "app.worker.tasks.merge.merge_results": 1,
    "app.worker.tasks.reconcile.reconcile_and_translate": 0,
    "app.worker.tasks.translate.translate_and_provision": 0,
    "app.worker.tasks.rag.index_document": 0,
}


def _extract_job_id(sender, args, kwargs) -> str | None:
    """Extract job_id from task arguments using the index map."""
    task_name = getattr(sender, "name", str(sender))
    idx = _JOB_ID_ARG_INDEX.get(task_name)

    if idx is not None and args and len(args) > idx:
        return str(args[idx])

    # Fallback: check kwargs
    return kwargs.get("job_id")


@task_failure.connect
def on_task_failure(sender, task_id, exception, args, kwargs, traceback, einfo, **kw):
    """Catch task failures that escape the task's own error handling.

    This covers hard timeouts, OOM kills, PaddleOCR segfaults, etc.
    Updates the job to FAILED and publishes the event to Redis PubSub.
    """
    job_id = _extract_job_id(sender, args, kwargs)
    if not job_id:
        logger.warning(
            f"task_failure signal for {getattr(sender, 'name', sender)} "
            f"but could not extract job_id from args={args}"
        )
        return

    error_msg = f"Task failed: {type(exception).__name__}: {exception}"
    logger.error(f"Job {job_id}: {error_msg}")

    try:
        from app.worker.db import publish_status, update_job

        update_job(job_id, status="FAILED", error_message=error_msg)
        publish_status(job_id, "FAILED", 0.0, error_message=error_msg)
    except Exception:
        logger.exception(f"Job {job_id}: failed to update status in failure callback")
