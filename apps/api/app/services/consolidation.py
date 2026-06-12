"""ParseGrid — Dataset Consolidation pure logic.

Three deterministic pieces, no IO, no LLM:
- allocate_sampling_budget: split the profiling page budget across documents
- union_buckets: merge per-document extraction buckets for reconciliation
- build_compat_report: measure how well an appended file fits the locked model
"""

from __future__ import annotations

from typing import Any

from app.schemas.extraction_model import DatabaseModel

SAMPLING_FLOOR = 3


def allocate_sampling_budget(
    page_counts: dict[str, int], budget: int = 15, floor: int = SAMPLING_FLOOR
) -> dict[str, int]:
    """Split `budget` sampled pages across documents, proportional to size.

    Every document gets min(floor, its page count). Remaining budget goes one
    page at a time to the document with the most unsampled pages (ties broken
    by document id), so the result is deterministic. When floors alone exceed
    the budget, floors win — small documents always keep their anchors.
    """
    if not page_counts:
        return {}
    alloc = {doc: min(floor, max(count, 0)) for doc, count in page_counts.items()}
    remaining = budget - sum(alloc.values())
    while remaining > 0:
        candidates = [d for d in page_counts if alloc[d] < page_counts[d]]
        if not candidates:
            break
        best = sorted(candidates, key=lambda d: (-(page_counts[d] - alloc[d]), d))[0]
        alloc[best] += 1
        remaining -= 1
    return alloc


def union_buckets(
    doc_buckets: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, list[dict]], dict[str, dict[str, list[int]]]]:
    """Merge per-document bucket payloads into reconcile_model inputs.

    Each payload has the shape
    `{"tables": {tbl: {"rows": [...], "chunk_pages": {chunk_key: [pages]}}}}`.
    Chunk keys and row `__chunk_index` markers are normalized to strings so
    legacy (integer-keyed) buckets and document-prefixed keys coexist.
    """
    bucketed_rows: dict[str, list[dict]] = {}
    chunk_pages: dict[str, dict[str, list[int]]] = {}
    for _document_id, payload in doc_buckets:
        tables = (payload or {}).get("tables") or {}
        for table_name, bucket in tables.items():
            rows = bucket.get("rows") or []
            for row in rows:
                row = dict(row)
                if "__chunk_index" in row:
                    row["__chunk_index"] = str(row["__chunk_index"])
                bucketed_rows.setdefault(table_name, []).append(row)
            pages = bucket.get("chunk_pages") or {}
            chunk_pages.setdefault(table_name, {}).update(
                {str(k): list(v) for k, v in pages.items()}
            )
    return bucketed_rows, chunk_pages


def build_compat_report(
    buckets: dict[str, list[dict]],
    locked_model: DatabaseModel,
    *,
    max_pk_null_ratio: float,
    dataset_histogram: dict[str, int] | None = None,
    document_histogram: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Measure how well an appended document's extraction fits the model.

    Pull-in rule (any one triggers needs_review):
    - zero rows extracted in total
    - more than `max_pk_null_ratio` of rows missing a primary-key component
    - every PK-bearing table received zero rows
    Histogram drift between the document and the dataset profile is recorded
    but never blocks on its own.
    """
    pk_columns = {
        t.table_name: [c.name for c in t.columns if c.is_primary_key] for t in locked_model.tables
    }
    pk_tables = sorted(t for t, cols in pk_columns.items() if cols)

    rows_per_table = {t: len(rows) for t, rows in buckets.items() if rows}
    total_rows = sum(rows_per_table.values())

    pk_null_rows = 0
    for table_name, rows in buckets.items():
        cols = pk_columns.get(table_name) or []
        if not cols:
            continue
        for row in rows:
            if any(row.get(c) in (None, "") for c in cols):
                pk_null_rows += 1
    pk_null_ratio = (pk_null_rows / total_rows) if total_rows else 0.0

    empty_pk_tables = [t for t in pk_tables if not rows_per_table.get(t)]

    reasons: list[str] = []
    if total_rows == 0:
        reasons.append("no rows extracted from this document")
    if total_rows and pk_null_ratio > max_pk_null_ratio:
        reasons.append(
            f"{pk_null_ratio:.0%} of rows are missing primary-key components "
            f"(threshold {max_pk_null_ratio:.0%})"
        )
    if total_rows and pk_tables and len(empty_pk_tables) == len(pk_tables):
        reasons.append("no primary-key table received any rows")

    profile_drift = None
    if dataset_histogram and document_histogram:
        profile_drift = {
            "dataset": dataset_histogram,
            "document": document_histogram,
        }

    return {
        "total_rows": total_rows,
        "rows_per_table": rows_per_table,
        "pk_null_rows": pk_null_rows,
        "pk_null_ratio": round(pk_null_ratio, 4),
        "pk_tables": pk_tables,
        "empty_pk_tables": empty_pk_tables,
        "profile_drift": profile_drift,
        "reasons": reasons,
        "needs_review": bool(reasons),
    }
