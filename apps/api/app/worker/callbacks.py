"""ParseGrid — Celery task lifecycle callbacks.

Hooks into Celery's signal system to update job status in the metadata
database whenever tasks start, succeed, or fail.
"""

from celery.signals import task_failure, task_success

# TODO: Phase 2 — Implement callbacks that update job records in PostgreSQL
# These callbacks run in the worker process and need a sync DB session.

# @task_success.connect
# def on_task_success(sender, result, **kwargs):
#     """Update job status on task completion."""
#     pass

# @task_failure.connect
# def on_task_failure(sender, exception, **kwargs):
#     """Update job status to FAILED on task failure."""
#     pass
