"""ParseGrid — Qdrant output provider (VECTOR).

Stores each reconciled row as a vector point in a per-job collection.
The payload keeps original row data and table metadata for downstream use.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import settings
from app.providers import BaseOutputProvider, ProvisionResult
from app.providers.factory import get_embedding_provider
from app.schemas.extraction_model import DatabaseModel, TableDef

_PROVENANCE_COLUMNS = ("source_page_numbers", "extraction_confidence", "reconciliation_notes")


class QdrantOutputProvider(BaseOutputProvider):
    """Provisions extracted rows into Qdrant collections."""

    def test_connection(self, connection_string: str) -> bool:
        url, api_key = _parse_qdrant_connection(connection_string)
        client = self._build_client(url, api_key)
        # Raises if unreachable / unauthorized.
        client.get_collections()
        return True

    def provision(
        self,
        schema_name: str,
        ddl_statements: list[str],
        data: dict[str, list[dict[str, Any]]],
        model: DatabaseModel,
    ) -> ProvisionResult:
        del ddl_statements  # VECTOR path does not execute SQL DDL.

        collection_name = schema_name
        url, api_key = _parse_qdrant_connection(connection_string=None)
        client = self._build_client(url, api_key)

        embedder = get_embedding_provider()
        dimension = embedder.dimension

        if _collection_exists(client, collection_name):
            client.delete_collection(collection_name=collection_name)

        client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=dimension,
                distance=qmodels.Distance.COSINE,
            ),
        )

        texts: list[str] = []
        point_payloads: list[dict[str, Any]] = []
        point_ids: list[str] = []
        table_defs = {t.table_name: t for t in model.tables}

        for table_name, rows in data.items():
            table_def = table_defs.get(table_name)
            if not table_def:
                continue
            for idx, row in enumerate(rows):
                texts.append(_canonical_row_text(table_name, row, table_def))
                point_payloads.append(
                    {
                        "job_scope": schema_name,
                        "table_name": table_name,
                        "row_index": idx,
                        "row": _coerce_payload(row),
                    }
                )
                point_ids.append(_point_id(table_name, idx, row))

        if texts:
            vectors = embedder.embed_texts(texts)
            if len(vectors) != len(texts):
                raise ValueError(
                    "Embedding provider returned mismatched vector count: "
                    f"expected {len(texts)}, got {len(vectors)}"
                )

            batch_size = 100
            for i in range(0, len(texts), batch_size):
                batch_points = [
                    qmodels.PointStruct(
                        id=point_ids[j],
                        vector=vectors[j],
                        payload=point_payloads[j],
                    )
                    for j in range(i, min(i + batch_size, len(texts)))
                ]
                client.upsert(
                    collection_name=collection_name,
                    points=batch_points,
                    wait=True,
                )

        summary = (
            "VECTOR PROVISION SUMMARY\n"
            f"collection={collection_name}\n"
            f"points_upserted={len(texts)}\n"
            f"embedding_dim={dimension}\n"
            f"tables={','.join(t.table_name for t in model.tables)}"
        )
        return ProvisionResult(
            connection_string=f"{url.rstrip('/')}/collections/{collection_name}",
            rows_inserted=len(texts),
            schema_name=collection_name,
            ddl_executed=summary,
        )

    def delete_output(self, schema_name: str) -> None:
        url, api_key = _parse_qdrant_connection(connection_string=None)
        client = self._build_client(url, api_key)
        if _collection_exists(client, schema_name):
            client.delete_collection(collection_name=schema_name)

    def _build_client(self, url: str, api_key: str | None) -> QdrantClient:
        return QdrantClient(url=url, api_key=api_key)


def _parse_qdrant_connection(connection_string: str | None) -> tuple[str, str | None]:
    if not connection_string:
        return settings.qdrant_url, settings.qdrant_api_key

    parsed = urlparse(connection_string)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid Qdrant connection string: {connection_string!r}")

    query = parse_qs(parsed.query)
    api_key = query.get("api_key", [None])[0] or settings.qdrant_api_key
    return f"{parsed.scheme}://{parsed.netloc}", api_key


def _collection_exists(client: QdrantClient, collection_name: str) -> bool:
    try:
        client.get_collection(collection_name=collection_name)
        return True
    except Exception:
        return False


def _point_id(table_name: str, row_index: int, row: dict[str, Any]) -> str:
    seed = json.dumps(
        {
            "table_name": table_name,
            "row_index": row_index,
            "row": row,
        },
        sort_keys=True,
        default=str,
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex


def _canonical_row_text(table_name: str, row: dict[str, Any], table: TableDef) -> str:
    declared = [c.name for c in table.columns]
    ordered = declared + [c for c in _PROVENANCE_COLUMNS if c in row]
    seen = set(ordered)
    extras = sorted(k for k in row.keys() if k not in seen)

    lines: list[str] = [f"table: {table_name}"]
    for col in ordered + extras:
        if col not in row:
            continue
        value = row[col]
        if value is None:
            continue
        lines.append(f"{col}: {_render_text_value(value)}")
    return "\n".join(lines)


def _render_text_value(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def _coerce_payload(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_coerce_payload(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce_payload(v) for k, v in value.items()}
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    return str(value)

