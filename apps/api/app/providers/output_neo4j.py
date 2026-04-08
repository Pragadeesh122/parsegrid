"""ParseGrid — Neo4j output provider (GRAPH).

Maps the reconciled Phase 7 multi-table payload into a scoped Neo4j subgraph.
All nodes/edges carry `__job_scope` for per-job logical isolation.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

from neo4j import GraphDatabase

from app.core.config import settings
from app.providers import BaseOutputProvider, ProvisionResult
from app.schemas.extraction_model import DatabaseModel, TableDef

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Neo4jOutputProvider(BaseOutputProvider):
    """Provisions extracted rows to Neo4j as a scoped property graph."""

    def test_connection(self, connection_string: str) -> bool:
        uri, user, password, database = _parse_neo4j_connection(
            connection_string=connection_string
        )
        driver = self._build_driver(uri, user, password)
        try:
            with driver.session(database=database) as session:
                session.run("RETURN 1 AS ok").consume()
            return True
        finally:
            driver.close()

    def provision(
        self,
        schema_name: str,
        ddl_statements: list[str],
        data: dict[str, list[dict[str, Any]]],
        model: DatabaseModel,
    ) -> ProvisionResult:
        del ddl_statements  # Neo4j path is not DDL-driven.

        uri, user, password, database = _parse_neo4j_connection(connection_string=None)
        driver = self._build_driver(uri, user, password)

        node_count = 0
        edge_count = 0
        table_defs = {t.table_name: t for t in model.tables}
        primary_lookup: dict[tuple[str, str, str], str] = {}

        try:
            with driver.session(database=database) as session:
                for table in model.tables:
                    _assert_identifier(table.table_name, kind="table")
                    rows = data.get(table.table_name, [])
                    for idx, row in enumerate(rows):
                        row_key = _build_row_key(table, row, idx)
                        props = {
                            "__job_scope": schema_name,
                            "__row_key": row_key,
                            "__table": table.table_name,
                            **_coerce_props(row),
                        }
                        session.run(
                            f"MERGE (n:`{table.table_name}` "
                            "{__job_scope: $scope, __row_key: $row_key}) "
                            "SET n += $props",
                            scope=schema_name,
                            row_key=row_key,
                            props=props,
                        ).consume()
                        node_count += 1

                        for pk_col in (c.name for c in table.columns if c.is_primary_key):
                            pk_value = row.get(pk_col)
                            if pk_value is None:
                                continue
                            primary_lookup[(
                                table.table_name,
                                pk_col,
                                _lookup_key(pk_value),
                            )] = row_key

                for rel_idx, rel in enumerate(model.relationships):
                    if not rel.enabled:
                        continue
                    _assert_identifier(rel.source_table, kind="table")
                    _assert_identifier(rel.references_table, kind="table")
                    rel_type = _relationship_type(rel.source_table, rel.references_table)

                    src_table_def = table_defs.get(rel.source_table)
                    src_rows = data.get(rel.source_table, [])
                    if not src_table_def or not src_rows:
                        continue

                    for row_idx, row in enumerate(src_rows):
                        source_value = row.get(rel.source_column)
                        if source_value is None:
                            continue
                        target_key = primary_lookup.get(
                            (
                                rel.references_table,
                                rel.references_column,
                                _lookup_key(source_value),
                            )
                        )
                        if not target_key:
                            continue

                        source_key = _build_row_key(src_table_def, row, row_idx)
                        edge_key = f"{source_key}|{target_key}|{rel_idx}"
                        session.run(
                            f"MATCH (s:`{rel.source_table}` "
                            "{__job_scope: $scope, __row_key: $source_key}) "
                            f"MATCH (t:`{rel.references_table}` "
                            "{__job_scope: $scope, __row_key: $target_key}) "
                            f"MERGE (s)-[r:`{rel_type}` "
                            "{__job_scope: $scope, __edge_key: $edge_key}]->(t) "
                            "SET r.source_column = $source_column, "
                            "r.references_column = $references_column, "
                            "r.link_basis = $link_basis, "
                            "r.nullable = $nullable",
                            scope=schema_name,
                            source_key=source_key,
                            target_key=target_key,
                            edge_key=edge_key,
                            source_column=rel.source_column,
                            references_column=rel.references_column,
                            link_basis=rel.link_basis,
                            nullable=rel.nullable,
                        ).consume()
                        edge_count += 1
        finally:
            driver.close()

        summary = (
            "GRAPH PROVISION SUMMARY\n"
            f"scope={schema_name}\n"
            f"nodes_merged={node_count}\n"
            f"edges_merged={edge_count}\n"
            f"tables={','.join(t.table_name for t in model.tables)}"
        )
        return ProvisionResult(
            connection_string=f"{uri}/{database}?scope={schema_name}",
            rows_inserted=node_count,
            schema_name=schema_name,
            ddl_executed=summary,
        )

    def delete_output(self, schema_name: str) -> None:
        uri, user, password, database = _parse_neo4j_connection(connection_string=None)
        driver = self._build_driver(uri, user, password)
        try:
            with driver.session(database=database) as session:
                session.run(
                    "MATCH (n {__job_scope: $scope}) DETACH DELETE n",
                    scope=schema_name,
                ).consume()
        finally:
            driver.close()

    def _build_driver(self, uri: str, user: str, password: str):
        return GraphDatabase.driver(uri, auth=(user, password))


def _parse_neo4j_connection(
    connection_string: str | None,
) -> tuple[str, str, str, str]:
    """Parse Neo4j connection details from URI or settings defaults."""
    if not connection_string:
        return (
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
            settings.neo4j_database,
        )

    parsed = urlparse(connection_string)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"Invalid Neo4j connection string: {connection_string!r}")

    port = parsed.port or 7687
    uri = f"{parsed.scheme}://{parsed.hostname}:{port}"
    user = parsed.username or settings.neo4j_user
    password = parsed.password or settings.neo4j_password

    qs = parse_qs(parsed.query)
    database = (
        qs.get("database", [None])[0]
        or parsed.path.lstrip("/")
        or settings.neo4j_database
    )
    return uri, user, password, database


def _assert_identifier(value: str, kind: str) -> None:
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(f"Invalid {kind} identifier for Neo4j label/type: {value!r}")


def _relationship_type(source_table: str, references_table: str) -> str:
    rel_type = f"{source_table}_TO_{references_table}".upper()
    rel_type = re.sub(r"[^A-Z0-9_]", "_", rel_type)
    rel_type = rel_type.strip("_")
    if not rel_type or rel_type[0].isdigit():
        rel_type = f"REL_{rel_type}"
    _assert_identifier(rel_type, kind="relationship")
    return rel_type


def _lookup_key(value: Any) -> str:
    if value is None:
        return "__NULL__"
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def _build_row_key(table: TableDef, row: dict[str, Any], row_index: int) -> str:
    pk_cols = [c.name for c in table.columns if c.is_primary_key]
    if pk_cols and all(row.get(col) is not None for col in pk_cols):
        joined = "|".join(f"{col}={_lookup_key(row.get(col))}" for col in pk_cols)
        return f"pk:{table.table_name}:{joined}"

    seed = json.dumps(
        {
            "table": table.table_name,
            "row_index": row_index,
            "row": row,
        },
        sort_keys=True,
        default=str,
    )
    return f"row:{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex}"


def _coerce_props(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _coerce_value(v) for k, v in row.items()}


def _coerce_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_coerce_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce_value(v) for k, v in value.items()}
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    return str(value)

