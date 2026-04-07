"""ParseGrid — Deterministic reconciliation (Phase 7).

Pure Python helpers that turn raw per-table extraction output into a
clean, FK-resolved dataset ready for provisioning. No LLM is used here —
the spec calls for deterministic-only reconciliation in the MVP.

Pipeline:
    1. normalize_value             — coerce types and clean whitespace
    2. canonicalize_parents        — composite-natural-key dedupe
    3. resolve_foreign_keys        — fill child FK columns from parent keys
    4. add_provenance              — populate source_page_numbers / notes
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from dateutil import parser as dateutil_parser

from app.schemas.extraction_model import (
    ColumnDef,
    DatabaseModel,
    RelationshipDef,
    TableDef,
)

logger = logging.getLogger(__name__)


_TRUE_VALUES = {"true", "t", "yes", "y", "1"}
_FALSE_VALUES = {"false", "f", "no", "n", "0"}
_CURRENCY_AND_THOUSANDS_RE = re.compile(r"[\u2212$€£¥,\s]")


# ---------------------------------------------------------------------------
# 1. Per-value normalization
# ---------------------------------------------------------------------------


def normalize_value(value: Any, column_type: str) -> Any:
    """Coerce a raw extracted value to its canonical form for `column_type`.

    Returns None when the value cannot be parsed — callers may attach a
    reconciliation note for the row.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None

    if column_type == "string":
        if not isinstance(value, str):
            return str(value)
        return unicodedata.normalize("NFC", value.strip())

    if column_type == "date":
        if isinstance(value, str):
            try:
                return dateutil_parser.parse(value, fuzzy=True).date().isoformat()
            except (ValueError, OverflowError):
                return None
        return None

    if column_type == "integer":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        cleaned = _CURRENCY_AND_THOUSANDS_RE.sub("", str(value))
        try:
            return int(float(cleaned))
        except ValueError:
            return None

    if column_type == "float":
        if isinstance(value, (int, float)):
            return float(value)
        cleaned = _CURRENCY_AND_THOUSANDS_RE.sub("", str(value))
        try:
            return float(cleaned)
        except ValueError:
            return None

    if column_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        s = str(value).strip().lower()
        if s in _TRUE_VALUES:
            return True
        if s in _FALSE_VALUES:
            return False
        return None

    return value


def normalize_row(row: dict[str, Any], table: TableDef) -> dict[str, Any]:
    """Apply `normalize_value` to every declared column on a row.

    Unknown keys (e.g., link-key columns added by extract_table) pass through
    untouched as strings.
    """
    out = dict(row)
    for col in table.columns:
        if col.name in out:
            out[col.name] = normalize_value(out[col.name], col.type)
    return out


# ---------------------------------------------------------------------------
# 2. Composite-natural-key dedupe
# ---------------------------------------------------------------------------


def _natural_key(row: dict[str, Any], pk_columns: list[str]) -> tuple | None:
    """Build a comparable natural-key tuple for a row.

    String comparisons are case-insensitive and whitespace-stripped so
    "Acme Corp " and "acme corp" collide deterministically. Returns None
    if any PK component is missing.
    """
    parts: list[Any] = []
    for col in pk_columns:
        val = row.get(col)
        if val is None:
            return None
        if isinstance(val, str):
            parts.append(val.strip().lower())
        else:
            parts.append(val)
    return tuple(parts)


def canonicalize_parents(
    rows: list[dict[str, Any]], table: TableDef
) -> tuple[list[dict[str, Any]], list[str]]:
    """Dedupe rows on the composite of `is_primary_key` columns.

    The first occurrence wins; subsequent rows with the same key are
    discarded but recorded as conflicts in the returned notes list.

    Tables with no PK columns get fingerprint-based dedupe instead.
    """
    notes: list[str] = []
    pk_columns = [c.name for c in table.columns if c.is_primary_key]

    if not pk_columns:
        # Fall back to a coarse fingerprint over the first three string-ish values.
        seen_fingerprints: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for row in rows:
            fp = _fingerprint(row)
            if fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                deduped.append(row)
        return deduped, notes

    seen: dict[tuple, dict[str, Any]] = {}
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = _natural_key(row, pk_columns)
        if key is None:
            # Missing PK component — keep the row but tag it.
            row.setdefault("__notes", []).append(
                f"row missing primary-key component(s) {pk_columns}"
            )
            deduped.append(row)
            continue
        if key in seen:
            notes.append(
                f"duplicate {table.table_name} row dropped for natural key {key}"
            )
            continue
        seen[key] = row
        deduped.append(row)
    return deduped, notes


def _fingerprint(record: dict[str, Any]) -> str:
    values: list[str] = []
    for v in record.values():
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            values.append(v.strip().lower()[:50])
            if len(values) >= 3:
                break
    return "|".join(values) if values else str(hash(frozenset(record.items())))


# ---------------------------------------------------------------------------
# 3. FK resolution
# ---------------------------------------------------------------------------


def resolve_foreign_keys(
    tables: dict[str, list[dict[str, Any]]],
    table_defs: dict[str, TableDef],
    relationships: list[RelationshipDef],
) -> None:
    """For every enabled relationship, validate that each child row's
    `source_column` value matches a known parent natural key.

    On miss:
        - nullable=True: leave the value (already populated by extraction)
          and append a reconciliation note.
        - nullable=False: leave the value, append an error note. The row is
          still inserted; we never fabricate FK values.

    Mutates `tables[child]` rows in place by adding `__notes` entries.
    """
    for rel in relationships:
        if not rel.enabled:
            continue
        parent_rows = tables.get(rel.references_table, [])
        child_rows = tables.get(rel.source_table, [])
        if not parent_rows or not child_rows:
            continue

        parent_def = table_defs.get(rel.references_table)
        if parent_def is None:
            continue

        # Build a lookup of valid parent natural keys for the referenced column.
        ref_col_def = next(
            (c for c in parent_def.columns if c.name == rel.references_column),
            None,
        )
        if ref_col_def is None:
            continue

        valid_keys: set[Any] = set()
        for parent in parent_rows:
            v = parent.get(rel.references_column)
            if v is None:
                continue
            if isinstance(v, str):
                valid_keys.add(v.strip().lower())
            else:
                valid_keys.add(v)

        for child in child_rows:
            raw = child.get(rel.source_column)
            if raw is None:
                continue
            needle = raw.strip().lower() if isinstance(raw, str) else raw
            if needle not in valid_keys:
                msg = (
                    f"FK {rel.source_table}.{rel.source_column}={raw!r} "
                    f"has no matching {rel.references_table}.{rel.references_column}"
                )
                child.setdefault("__notes", []).append(msg)
                if not rel.nullable:
                    logger.warning(f"non-nullable FK miss: {msg}")


# ---------------------------------------------------------------------------
# 4. Provenance
# ---------------------------------------------------------------------------


def add_provenance(
    rows: list[dict[str, Any]],
    chunk_pages_by_index: dict[int, list[int]],
) -> list[dict[str, Any]]:
    """Populate `source_page_numbers`, `extraction_confidence`, and
    `reconciliation_notes` on each row.

    Rows carry a `__chunk_index` and a `__notes` list set by upstream stages;
    those internal markers are stripped here so the row is ready to insert.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        chunk_index = row.pop("__chunk_index", None)
        notes = row.pop("__notes", None)
        clean = dict(row)
        if chunk_index is not None and chunk_index in chunk_pages_by_index:
            clean["source_page_numbers"] = list(chunk_pages_by_index[chunk_index])
        else:
            clean["source_page_numbers"] = None
        clean["extraction_confidence"] = None
        clean["reconciliation_notes"] = "; ".join(notes) if notes else None
        out.append(clean)
    return out


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def reconcile_model(
    bucketed_rows: dict[str, list[dict[str, Any]]],
    chunk_pages_by_index: dict[str, dict[int, list[int]]],
    locked_model: DatabaseModel,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """End-to-end deterministic reconciliation.

    Args:
        bucketed_rows: {table_name: [raw row, ...]} (rows already carry
            `__chunk_index` markers).
        chunk_pages_by_index: {table_name: {chunk_index: [page_numbers]}}
            for provenance lookup.
        locked_model: The full DatabaseModel.

    Returns:
        ({table_name: [reconciled row, ...]}, run_notes)
    """
    run_notes: list[str] = []
    table_defs = {t.table_name: t for t in locked_model.tables}

    # 1. Per-value normalization
    normalized: dict[str, list[dict[str, Any]]] = {}
    for table_name, rows in bucketed_rows.items():
        table = table_defs.get(table_name)
        if table is None:
            run_notes.append(f"orphan extraction bucket {table_name!r} dropped")
            continue
        normalized[table_name] = [normalize_row(r, table) for r in rows]

    # 2. Canonicalize each table on its primary key
    canonicalized: dict[str, list[dict[str, Any]]] = {}
    for table_name, rows in normalized.items():
        table = table_defs[table_name]
        deduped, notes = canonicalize_parents(rows, table)
        canonicalized[table_name] = deduped
        run_notes.extend(notes)

    # 3. FK resolution
    resolve_foreign_keys(canonicalized, table_defs, locked_model.relationships)

    # 4. Provenance + clean
    finalized: dict[str, list[dict[str, Any]]] = {}
    for table_name, rows in canonicalized.items():
        finalized[table_name] = add_provenance(
            rows, chunk_pages_by_index.get(table_name, {})
        )

    return finalized, run_notes
