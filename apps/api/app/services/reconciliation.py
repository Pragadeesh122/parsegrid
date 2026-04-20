"""ParseGrid — Reconciliation (Phase 7).

Pure Python helpers that turn raw per-table extraction output into a
clean, FK-resolved dataset ready for provisioning.

Pipeline:
    1. normalize_value             — coerce types and clean whitespace
    2. entity_resolution           — LLM-assisted merge for any table with
                                     2+ rows (gpt-5.4, always fires)
    3. resolve_foreign_keys        — repair child FK values to match parent
                                     canonical keys (token-set fuzzy match)
    4. canonicalize_parents        — composite-natural-key dedupe w/ merge
                                     (catches collisions from step 3)
    5. add_provenance              — populate source_page_numbers / notes
"""

from __future__ import annotations

import json
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
    # LLMs sometimes return the literal string "null" / "none" / "n/a" instead
    # of JSON null — treat all of these as missing values.
    if isinstance(value, str) and value.strip().lower() in {
        "null",
        "none",
        "n/a",
        "na",
        "undefined",
    }:
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
    """Dedupe rows on the composite of ``is_primary_key`` columns.

    When ``entity_resolution`` has run successfully, rows are already one
    per entity so this function is a no-op safety net.  If entity resolution
    was skipped or its LLM call failed, any remaining collisions are merged
    field-by-field: null fields in the first-seen row are filled from the
    duplicate so the richest version of the record survives.

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
            # Merge: fill null fields in the existing row from this duplicate.
            existing = seen[key]
            merged_fields: list[str] = []
            for field, value in row.items():
                if field.startswith("__"):
                    continue
                if existing.get(field) is None and value is not None:
                    existing[field] = value
                    merged_fields.append(field)
            if merged_fields:
                notes.append(
                    f"merged {table.table_name} duplicate for key {key}: filled {merged_fields}"
                )
            else:
                notes.append(f"duplicate {table.table_name} row for key {key} had no new fields")
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
# 2a. Entity resolution (LLM-assisted, fires only when PKs are non-unique)
# ---------------------------------------------------------------------------


def needs_resolution(rows: list[dict[str, Any]], pk_columns: list[str]) -> bool:
    """Returns True when there are multiple rows — always worth asking the LLM.

    A single row can never be a duplicate, so we skip the LLM only in that
    case.  For two or more rows the LLM resolves name-format variants
    ("Last, First" vs "First Last"), partial duplicates, and genuine distinct
    entities in one call — far more reliable than any string heuristic.
    """
    return len(rows) > 1


def entity_resolution(
    rows: list[dict[str, Any]],
    table: TableDef,
    pk_columns: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Group rows by entity and return one LLM-merged row per entity.

    Fires whenever there are two or more rows — the LLM handles all of:
    - Same entity extracted with different PK string formats across chunks
      (e.g. "Last, First" vs "First Last").
    - Fields scattered across sparse and rich chunks (picks best non-null
      value per field rather than blindly filling nulls).
    - Genuinely distinct entities that happen to share a table.

    Rows with all-null PK columns (partial extractions) are included and the
    LLM is instructed to absorb them into the most contextually appropriate
    entity — no custom heuristics needed.

    Uses gpt-5.4.  On any LLM failure the original rows are returned
    unchanged so the pipeline degrades gracefully to field-by-field merge in
    ``canonicalize_parents()``.
    """
    notes: list[str] = []
    if not needs_resolution(rows, pk_columns):
        return rows, notes

    try:
        from openai import OpenAI

        from app.core.config import settings

        client = OpenAI(api_key=settings.openai_api_key)
    except Exception as exc:  # pragma: no cover
        notes.append(f"entity_resolution: could not initialise OpenAI client: {exc}")
        return rows, notes

    col_names = [c.name for c in table.columns]

    # Strip internal __ markers before sending to the LLM.
    rows_for_llm = [
        {
            "row_index": i,
            "data": {k: v for k, v in row.items() if not k.startswith("__")},
        }
        for i, row in enumerate(rows)
    ]

    system_prompt = (
        "You are an entity resolution and merge assistant for a data pipeline. "
        "You receive rows extracted from different sections of the same document. "
        "Some rows represent the same real-world entity extracted multiple times "
        "(possibly with different name formats, word order, or abbreviations). "
        "Your tasks:\n"
        "1. Group rows that refer to the same entity.\n"
        "2. For each group produce one merged row: prefer non-null over null; "
        "for two conflicting non-null values prefer the longer/more specific one.\n"
        "3. Keep rows for distinct entities separate.\n"
        "4. If a row has null values for ALL primary key columns it is a partial "
        "extraction — merge its non-null fields into the most contextually appropriate "
        "entity rather than treating it as a separate record.\n"
        'Return JSON: {"entities": [{"row_indices": [<int>, ...], '
        '"merged": {<field>: <value>, ...}}, ...]}'
    )

    user_prompt = (
        f"Table: {table.table_name}\n"
        f"Primary key columns: {pk_columns}\n"
        f"All columns: {col_names}\n\n"
        f"Rows:\n{json.dumps(rows_for_llm, indent=2)}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            timeout=120.0,
        )
        result = json.loads(resp.choices[0].message.content or "{}")
        entities: list[dict] = result.get("entities", [])
    except Exception as exc:
        notes.append(
            f"entity_resolution({table.table_name}): LLM call failed ({exc}); "
            "falling back to field-by-field merge"
        )
        return rows, notes

    merged_rows: list[dict[str, Any]] = []
    accounted: set[int] = set()

    for entity in entities:
        row_indices = [i for i in entity.get("row_indices", []) if isinstance(i, int)]
        merged_data = entity.get("merged")
        if not row_indices or not isinstance(merged_data, dict):
            continue

        # Preserve internal markers (e.g. __chunk_index) from the richest
        # source row — the one with the most non-null fields.
        source_rows = [rows[i] for i in row_indices if i < len(rows)]
        richest = max(
            source_rows,
            key=lambda r: sum(1 for k, v in r.items() if not k.startswith("__") and v is not None),
        )

        merged_row: dict[str, Any] = dict(merged_data)
        for k, v in richest.items():
            if k.startswith("__"):
                merged_row[k] = v

        merged_rows.append(merged_row)
        accounted.update(row_indices)

    # Safety net: pass through any rows the LLM didn't account for.
    for i, row in enumerate(rows):
        if i not in accounted:
            merged_rows.append(row)
            notes.append(
                f"entity_resolution({table.table_name}): row {i} not in LLM "
                "response, passed through unchanged"
            )

    notes.append(
        f"entity_resolution({table.table_name}): {len(rows)} rows → {len(merged_rows)} entities"
    )
    logger.info(
        f"entity_resolution({table.table_name}): {len(rows)} rows → {len(merged_rows)} entities"
    )
    return merged_rows, notes


# ---------------------------------------------------------------------------
# 3. FK resolution
# ---------------------------------------------------------------------------


def _token_set(value: str) -> frozenset[str]:
    """Normalise a name to a frozenset of lowercase tokens.

    Strips punctuation used as name separators (comma, period) so that
    "Surendiran, Pragadeesh" and "Pragadeesh Surendiran" produce the same set.
    """
    cleaned = re.sub(r"[,.\s]+", " ", value.strip().lower())
    return frozenset(cleaned.split())


def resolve_foreign_keys(
    tables: dict[str, list[dict[str, Any]]],
    table_defs: dict[str, TableDef],
    relationships: list[RelationshipDef],
) -> None:
    """For every enabled relationship, validate and repair each child row's
    `source_column` value against the known parent natural keys.

    Repair strategy (string FKs only):
        1. Exact match after strip+lower → keep as-is.
        2. Token-set match (handles "Last, First" vs "First Last") → rewrite
           the child FK to the canonical parent key so the DB insert succeeds.
        3. No match → append a reconciliation note; the row is still inserted.

    Mutates `tables[child]` rows in place.
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

        ref_col_def = next(
            (c for c in parent_def.columns if c.name == rel.references_column),
            None,
        )
        if ref_col_def is None:
            continue

        # Build exact (lower) and fuzzy (token-set) lookups → canonical value.
        exact_lookup: dict[str, Any] = {}  # lower(v) → canonical v
        fuzzy_lookup: dict[frozenset, Any] = {}  # token_set(v) → canonical v
        for parent in parent_rows:
            v = parent.get(rel.references_column)
            if v is None:
                continue
            if isinstance(v, str):
                exact_lookup[v.strip().lower()] = v
                fuzzy_lookup[_token_set(v)] = v
            else:
                exact_lookup[v] = v

        for child in child_rows:
            raw = child.get(rel.source_column)
            if raw is None:
                continue

            if isinstance(raw, str):
                needle_exact = raw.strip().lower()
                if needle_exact in exact_lookup:
                    # Exact match — normalise casing to canonical.
                    child[rel.source_column] = exact_lookup[needle_exact]
                    continue

                needle_fuzzy = _token_set(raw)
                if needle_fuzzy in fuzzy_lookup:
                    canonical = fuzzy_lookup[needle_fuzzy]
                    child[rel.source_column] = canonical
                    child.setdefault("__notes", []).append(
                        f"FK {rel.source_table}.{rel.source_column}: "
                        f"repaired '{raw}' → '{canonical}' via token-set match"
                    )
                    logger.info(
                        f"FK repair: {rel.source_table}.{rel.source_column} '{raw}' → '{canonical}'"
                    )
                    continue

                # No match at all — log and annotate.
                msg = (
                    f"FK {rel.source_table}.{rel.source_column}={raw!r} "
                    f"has no matching {rel.references_table}.{rel.references_column}"
                )
                child.setdefault("__notes", []).append(msg)
                if not rel.nullable:
                    logger.warning(f"non-nullable FK miss: {msg}")
            else:
                if raw not in exact_lookup:
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

    # 2. Entity resolution — LLM merges any table with 2+ rows.
    #    Handles name-format variants, sparse vs rich chunks, and genuine
    #    distinct entities in one call.  No-op only for single-row tables.
    er_resolved: dict[str, list[dict[str, Any]]] = {}
    for table_name, rows in normalized.items():
        table = table_defs[table_name]
        pk_columns = [c.name for c in table.columns if c.is_primary_key]
        if pk_columns:
            resolved_rows, er_notes = entity_resolution(rows, table, pk_columns)
            er_resolved[table_name] = resolved_rows
            run_notes.extend(er_notes)
        else:
            er_resolved[table_name] = rows

    # 3. FK repair — normalise child FK values to match parent canonical keys.
    #    Must run before canonicalize_parents so that any PK values rewritten
    #    by the repair (e.g. "First Last" → "Last, First") are visible when we
    #    dedupe on natural keys in step 4.
    resolve_foreign_keys(er_resolved, table_defs, locked_model.relationships)

    # 4. Canonicalize each table on its primary key (merge on collision).
    #    Running after FK repair means collisions created by repair
    #    (two rows whose PKs were normalised to the same canonical value)
    #    are caught and merged here rather than causing a UniqueViolation.
    canonicalized: dict[str, list[dict[str, Any]]] = {}
    for table_name, rows in er_resolved.items():
        table = table_defs[table_name]
        deduped, notes = canonicalize_parents(rows, table)
        canonicalized[table_name] = deduped
        run_notes.extend(notes)

    # 5. Provenance + clean
    finalized: dict[str, list[dict[str, Any]]] = {}
    for table_name, rows in canonicalized.items():
        finalized[table_name] = add_provenance(rows, chunk_pages_by_index.get(table_name, {}))

    return finalized, run_notes
