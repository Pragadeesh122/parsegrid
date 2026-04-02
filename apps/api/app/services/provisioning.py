"""ParseGrid — Database provisioning service.

Creates isolated PostgreSQL schemas per job and bulk inserts extracted data.
Uses CREATE SCHEMA (NOT CREATE DATABASE) per the architectural constraint.
"""

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_sync_engine():
    """Create a sync SQLAlchemy engine for provisioning operations."""
    sync_url = settings.database_url.replace("+asyncpg", "+psycopg2").replace(
        "postgresql+psycopg2", "postgresql"
    )
    return create_engine(sync_url)


def provision_and_insert(
    schema_name: str,
    ddl_statements: str,
    data: dict | list,
    json_schema: dict,
) -> str:
    """Create an isolated PG schema, execute DDL, bulk insert data.

    Args:
        schema_name: The schema name (e.g., job_<uuid>)
        ddl_statements: SQL DDL string from the Translator Agent
        data: The merged extraction data
        json_schema: The locked JSON schema (for table name inference)

    Returns:
        Connection string for the user to access their data.
    """
    engine = _get_sync_engine()

    try:
        with engine.connect() as conn:
            # 1. Create isolated schema
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
            conn.execute(text(f'SET search_path TO "{schema_name}"'))
            logger.info(f"Created schema: {schema_name}")

            # 2. Execute DDL statements
            # Split by semicolons but handle edge cases
            statements = _split_sql(ddl_statements)
            for stmt in statements:
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        conn.execute(text(stmt))
                    except Exception as e:
                        logger.warning(f"DDL statement failed (continuing): {e}\n  SQL: {stmt[:200]}")

            logger.info(f"Executed {len(statements)} DDL statements in {schema_name}")

            # 3. Bulk insert data
            _bulk_insert(conn, data, json_schema, schema_name)

            conn.commit()

    finally:
        engine.dispose()

    # 4. Generate connection string
    return _generate_connection_string(schema_name)


def _split_sql(sql: str) -> list[str]:
    """Split SQL string into individual statements."""
    # Remove markdown code fences if LLM wrapped the output
    sql = re.sub(r"```sql\s*", "", sql)
    sql = re.sub(r"```\s*", "", sql)

    statements = [s.strip() for s in sql.split(";") if s.strip()]
    return statements


def _bulk_insert(
    conn,
    data: dict | list,
    json_schema: dict,
    schema_name: str,
):
    """Insert extracted data into the provisioned tables."""
    # Find the items array
    items_key = "items"
    for k, v in json_schema.get("properties", {}).items():
        if v.get("type") == "array":
            items_key = k
            break

    records = data.get(items_key, []) if isinstance(data, dict) else data
    if not records:
        logger.warning(f"No records to insert for {schema_name}")
        return

    # Infer table name from schema title or items key
    table_name = json_schema.get("title", items_key).lower()
    table_name = re.sub(r"[^a-z0-9_]", "_", table_name)

    # Get columns from first record
    if not records or not isinstance(records[0], dict):
        return

    columns = list(records[0].keys())
    col_names = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(f":{c}" for c in columns)

    insert_sql = f'INSERT INTO "{schema_name}"."{table_name}" ({col_names}) VALUES ({placeholders})'

    inserted = 0
    for record in records:
        try:
            # Serialize any nested objects/arrays to JSON strings
            clean_record = {}
            for k, v in record.items():
                if isinstance(v, (dict, list)):
                    clean_record[k] = json.dumps(v)
                else:
                    clean_record[k] = v

            conn.execute(text(insert_sql), clean_record)
            inserted += 1
        except Exception as e:
            logger.warning(f"Insert failed for record (skipping): {e}")

    logger.info(f"Inserted {inserted}/{len(records)} records into {schema_name}.{table_name}")


def _generate_connection_string(schema_name: str) -> str:
    """Generate a user-facing connection string for the provisioned schema."""
    parsed = urlparse(
        settings.database_url.replace("+asyncpg", "").replace("+psycopg2", "")
    )

    # Build connection string with search_path set to the job schema
    conn_str = (
        f"postgresql://{parsed.username}:{parsed.password}"
        f"@{parsed.hostname}:{parsed.port or 5432}"
        f"{parsed.path}"
        f"?options=-csearch_path%3D{schema_name}"
    )

    return conn_str
