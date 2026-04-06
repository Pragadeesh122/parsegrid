"""ParseGrid — PostgreSQL output provider.

Creates isolated schemas per job, executes LLM-generated DDL,
and bulk inserts extracted data using parameterized queries.
"""

import json
import logging
import re
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.providers import BaseOutputProvider, ProvisionResult

logger = logging.getLogger(__name__)


def _get_sync_url() -> str:
    """Convert the async database URL to a synchronous one."""
    url = settings.database_url
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "+psycopg2")
    if url.startswith("postgresql+psycopg2"):
        url = url.replace("postgresql+psycopg2", "postgresql")
    return url


class PostgresOutputProvider(BaseOutputProvider):
    """Provisions extracted data into PostgreSQL.

    Each job gets an isolated schema (CREATE SCHEMA job_{uuid}) to prevent
    collisions. The connection string returned uses search_path to scope
    queries automatically.
    """

    def test_connection(self, connection_string: str) -> bool:
        """Attempt a lightweight connection to verify reachability."""
        engine = create_engine(connection_string, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        finally:
            engine.dispose()

    def provision(
        self,
        schema_name: str,
        ddl: str,
        data: dict | list,
        json_schema: dict,
    ) -> ProvisionResult:
        """Create schema, execute DDL, bulk insert, return connection string."""
        engine = create_engine(_get_sync_url())

        try:
            with engine.connect() as conn:
                # 1. Create isolated schema
                conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
                conn.execute(text(f'SET search_path TO "{schema_name}"'))
                logger.info(f"Created schema: {schema_name}")

                # 2. Execute DDL statements
                statements = _split_sql(ddl)
                for stmt in statements:
                    stmt = stmt.strip()
                    if stmt and not stmt.startswith("--"):
                        try:
                            conn.execute(text(stmt))
                        except Exception as e:
                            logger.warning(
                                f"DDL statement failed (continuing): {e}\n  SQL: {stmt[:200]}"
                            )

                logger.info(f"Executed {len(statements)} DDL statements in {schema_name}")

                # 3. Bulk insert data
                rows_inserted = _bulk_insert(conn, data, json_schema, schema_name)

                conn.commit()

        finally:
            engine.dispose()

        connection_string = _generate_connection_string(schema_name)

        return ProvisionResult(
            connection_string=connection_string,
            rows_inserted=rows_inserted,
            schema_name=schema_name,
            ddl_executed=ddl,
        )

    def delete_output(self, schema_name: str) -> None:
        """Drop a provisioned schema and all of its objects."""
        engine = create_engine(_get_sync_url())

        try:
            with engine.connect() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
                conn.commit()
                logger.info(f"Dropped provisioned schema: {schema_name}")
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_sql(sql: str) -> list[str]:
    """Split SQL string into individual statements, stripping markdown fences."""
    sql = re.sub(r"```sql\s*", "", sql)
    sql = re.sub(r"```\s*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


def _bulk_insert(
    conn,
    data: dict | list,
    json_schema: dict,
    schema_name: str,
) -> int:
    """Insert extracted data into the provisioned tables. Returns row count."""
    # Find the items array
    items_key = "items"
    for k, v in json_schema.get("properties", {}).items():
        if v.get("type") == "array":
            items_key = k
            break

    records = data.get(items_key, []) if isinstance(data, dict) else data
    if not records:
        logger.warning(f"No records to insert for {schema_name}")
        return 0

    if not isinstance(records[0], dict):
        return 0

    # Find the actual table name from the schema we just created
    result = conn.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = :schema"
    ), {"schema": schema_name})
    table_names = [r[0] for r in result]
    if table_names:
        table_name = table_names[0]
    else:
        # Fallback: infer from schema title
        table_name = json_schema.get("title", items_key).lower()
        table_name = re.sub(r"[^a-z0-9_]", "_", table_name)

    columns = list(records[0].keys())
    col_names = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(f":{c}" for c in columns)

    insert_sql = f'INSERT INTO "{schema_name}"."{table_name}" ({col_names}) VALUES ({placeholders})'

    inserted = 0
    for record in records:
        try:
            clean_record = {}
            for k, v in record.items():
                if isinstance(v, (dict, list)):
                    clean_record[k] = json.dumps(v)
                else:
                    clean_record[k] = v

            conn.execute(text("SAVEPOINT row_sp"))
            conn.execute(text(insert_sql), clean_record)
            conn.execute(text("RELEASE SAVEPOINT row_sp"))
            inserted += 1
        except Exception as e:
            conn.execute(text("ROLLBACK TO SAVEPOINT row_sp"))
            logger.warning(f"Insert failed for record (skipping): {e}")

    logger.info(f"Inserted {inserted}/{len(records)} records into {schema_name}.{table_name}")
    return inserted


def _generate_connection_string(schema_name: str) -> str:
    """Generate a user-facing connection string with search_path scoping."""
    parsed = urlparse(
        settings.database_url.replace("+asyncpg", "").replace("+psycopg2", "")
    )

    return (
        f"postgresql://{parsed.username}:{parsed.password}"
        f"@{parsed.hostname}:{parsed.port or 5432}"
        f"{parsed.path}"
        f"?options=-csearch_path%3D{schema_name}"
    )
