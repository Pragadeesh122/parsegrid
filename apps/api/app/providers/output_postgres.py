"""ParseGrid — PostgreSQL output provider (Phase 7).

Per job we provision an isolated schema (`job_{uuid}`), run the
deterministic DDL from `services.ddl.build_ddl`, and insert the
reconciled multi-table dataset in FK dependency order using
`graphlib.TopologicalSorter`.

Inserts use SAVEPOINT-per-row so a single bad row never aborts the
whole transaction.
"""

from __future__ import annotations

import json
import logging
from graphlib import CycleError, TopologicalSorter
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.providers import BaseOutputProvider, ProvisionResult
from app.schemas.extraction_model import DatabaseModel, TableDef

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
    """Provisions Phase 7 multi-table extracted data into PostgreSQL."""

    def test_connection(self, connection_string: str) -> bool:
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
        ddl_statements: list[str],
        data: dict[str, list[dict[str, Any]]],
        model: DatabaseModel,
    ) -> ProvisionResult:
        engine = create_engine(_get_sync_url())
        rows_inserted = 0
        ddl_audit = ";\n".join(ddl_statements) + ";"

        try:
            with engine.connect() as conn:
                # 1. Isolated schema.
                conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
                conn.execute(text(f'SET search_path TO "{schema_name}"'))
                logger.info(f"Created schema: {schema_name}")

                # 2. DDL — already validated and ordered (CREATE then ALTER).
                for stmt in ddl_statements:
                    stripped = stmt.strip()
                    if not stripped:
                        continue
                    try:
                        conn.execute(text(stripped))
                    except Exception as e:
                        logger.warning(
                            f"DDL statement failed (continuing): {e}\n  SQL: {stripped[:200]}"
                        )

                logger.info(
                    f"Executed {len(ddl_statements)} DDL statements in {schema_name}"
                )

                # 3. Insert rows in FK dependency order.
                table_defs = {t.table_name: t for t in model.tables}
                ordered_tables = _topological_table_order(model)

                for table_name in ordered_tables:
                    table_def = table_defs.get(table_name)
                    rows = data.get(table_name, [])
                    if not table_def or not rows:
                        continue
                    inserted = _insert_table(
                        conn=conn,
                        schema_name=schema_name,
                        table_def=table_def,
                        rows=rows,
                    )
                    rows_inserted += inserted

                conn.commit()

        finally:
            engine.dispose()

        return ProvisionResult(
            connection_string=_generate_connection_string(schema_name),
            rows_inserted=rows_inserted,
            schema_name=schema_name,
            ddl_executed=ddl_audit,
        )

    def delete_output(self, schema_name: str) -> None:
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


# Provenance columns added to every table by the DDL generator. Mirrored here
# so the row inserter knows about them. Keep in sync with `services/ddl.py`.
_PROVENANCE_COLUMNS = ("source_page_numbers", "extraction_confidence", "reconciliation_notes")


def _topological_table_order(model: DatabaseModel) -> list[str]:
    """Parent-before-child table order computed from enabled relationships.

    Falls back to declaration order if the dependency graph contains a cycle
    (which `validate_model` does not currently prevent — we just log and
    insert in declaration order so the job can still complete).
    """
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for table in model.tables:
        sorter.add(table.table_name)
    for rel in model.relationships:
        if not rel.enabled:
            continue
        if rel.source_table == rel.references_table:
            continue  # self-references don't constrain insert order
        # source depends on references → references comes first.
        sorter.add(rel.source_table, rel.references_table)
    try:
        return list(sorter.static_order())
    except CycleError as e:
        logger.warning(f"FK dependency cycle detected, falling back to declaration order: {e}")
        return [t.table_name for t in model.tables]


def _insert_table(
    conn,
    schema_name: str,
    table_def: TableDef,
    rows: list[dict[str, Any]],
) -> int:
    """Insert rows for one table using a row-level SAVEPOINT for resilience."""
    declared_columns = [c.name for c in table_def.columns]
    all_columns = list(declared_columns) + list(_PROVENANCE_COLUMNS)

    col_list = ", ".join(f'"{c}"' for c in all_columns)
    placeholders = ", ".join(f":{c}" for c in all_columns)
    insert_sql = (
        f'INSERT INTO "{schema_name}"."{table_def.table_name}" '
        f"({col_list}) VALUES ({placeholders})"
    )

    inserted = 0
    for row in rows:
        try:
            conn.execute(text("SAVEPOINT row_sp"))
            params = _build_params(row, all_columns)
            conn.execute(text(insert_sql), params)
            conn.execute(text("RELEASE SAVEPOINT row_sp"))
            inserted += 1
        except Exception as e:
            conn.execute(text("ROLLBACK TO SAVEPOINT row_sp"))
            logger.warning(
                f"Insert failed for row in {schema_name}.{table_def.table_name} "
                f"(skipping): {e}"
            )

    logger.info(
        f"Inserted {inserted}/{len(rows)} rows into {schema_name}.{table_def.table_name}"
    )
    return inserted


def _build_params(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    """Project a row dict onto the declared+provenance columns.

    Missing columns become None. JSONB columns get pre-serialized so they
    bind cleanly through psycopg2.
    """
    params: dict[str, Any] = {}
    for col in columns:
        v = row.get(col)
        if isinstance(v, (list, dict)):
            params[col] = json.dumps(v)
        else:
            params[col] = v
    return params


def _generate_connection_string(schema_name: str) -> str:
    parsed = urlparse(
        settings.database_url.replace("+asyncpg", "").replace("+psycopg2", "")
    )
    return (
        f"postgresql://{parsed.username}:{parsed.password}"
        f"@{parsed.hostname}:{parsed.port or 5432}"
        f"{parsed.path}"
        f"?options=-csearch_path%3D{schema_name}"
    )
