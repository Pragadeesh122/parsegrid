"""ParseGrid — Celery application configuration.

Dedicated queues per task type to prevent blocking:
- ocr:         Heavy OCR processing (LlamaParse / PaddleOCR)
- extraction:  LLM extraction tasks (Map phase)
- merge:       Programmatic JSON merge (Reduce phase)
- translation: DB translation + provisioning
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "parsegrid",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Task routing — dedicated queues
    task_routes={
        "app.worker.tasks.ocr.*": {"queue": "ocr"},
        "app.worker.tasks.extract.*": {"queue": "extraction"},
        "app.worker.tasks.merge.*": {"queue": "merge"},
        "app.worker.tasks.translate.*": {"queue": "translation"},
    },
    # Task limits
    task_time_limit=600,  # 10 min hard limit
    task_soft_time_limit=540,  # 9 min soft limit
    # Retry behavior
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Result expiration
    result_expires=86400,  # 24 hours
)

# Auto-discover tasks in app.worker.tasks package
celery_app.autodiscover_tasks(["app.worker.tasks"])
