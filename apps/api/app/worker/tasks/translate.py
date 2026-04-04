"""ParseGrid — Database translation and provisioning tasks.

Translates extracted JSON data to SQL DDL + INSERT statements,
provisions an isolated PostgreSQL schema, and generates a connection string.
"""

import logging

from app.worker.celery_app import celery_app
from app.worker.db import publish_status, update_job

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.translate.translate_and_provision",
    bind=True,
    queue="translation",
)
def translate_and_provision(
    self,
    job_id: str,
    merged_data: dict | list,
    schema: dict,
):
    """Translate extracted data to target format and provision output database.

    1. Generate DDL from schema via LLM
    2. CREATE SCHEMA job_{uuid} in shared PostgreSQL
    3. Execute DDL statements
    4. Bulk INSERT extracted data
    5. Generate connection string
    6. Update job to COMPLETED
    """
    try:
        publish_status(job_id, "TRANSLATING", 0.0)
        update_job(job_id, status="TRANSLATING", progress=0.0)

        # 1. Generate DDL via LLM
        from app.providers.factory import get_llm_provider

        llm = get_llm_provider()
        ddl = llm.generate_ddl(schema, "SQL")

        publish_status(job_id, "TRANSLATING", 30.0)
        logger.info(f"Job {job_id}: DDL generated ({len(ddl)} chars)")

        # 2. Provision schema + execute DDL + insert data
        publish_status(job_id, "PROVISIONING", 40.0)
        update_job(job_id, status="PROVISIONING", progress=40.0)

        from app.services.provisioning import provision_and_insert

        schema_name = f"job_{job_id.replace('-', '_')}"
        connection_string = provision_and_insert(
            schema_name=schema_name,
            ddl_statements=ddl,
            data=merged_data,
            json_schema=schema,
        )

        publish_status(job_id, "PROVISIONING", 80.0)

        # 3. Update job to COMPLETED
        update_job(
            job_id,
            status="COMPLETED",
            progress=100.0,
            output_schema_name=schema_name,
            connection_string=connection_string,
        )

        publish_status(
            job_id,
            "COMPLETED",
            100.0,
            connection_string=connection_string,
        )

        logger.info(f"Job {job_id}: COMPLETED → {connection_string}")

    except Exception as exc:
        logger.exception(f"Job {job_id}: translation/provisioning failed")
        publish_status(job_id, "FAILED", 0.0, error_message=str(exc))
        update_job(job_id, status="FAILED", error_message=str(exc))
        raise
