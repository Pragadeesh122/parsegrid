"""ParseGrid — Deterministic DDL generator (Phase 7).

Pure functions that translate a typed `DatabaseModel` into PostgreSQL
`CREATE TABLE` and `ALTER TABLE ... ADD CONSTRAINT` statements. The output
is byte-deterministic for a given (model, schema_name) pair so it can be
diffed and audited.

`generate_ddl` lives here, NOT in the LLM provider — Phase 7 deletes the
LLM-based DDL generation entirely.

The function also performs structural validation on relationships and
returns a possibly-modified `DatabaseModel`: relationships whose
`references_column` is not `is_primary_key=True` on the referenced table
are downgraded to `enabled=False` with a reconciliation note appended.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field

from app.schemas.extraction_model import (
    ColumnDef,
    ColumnType,
    DatabaseModel,
    RelationshipDef,
    TableDef,
)

# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[ColumnType, str] = {
    "string": "TEXT",
    "integer": "BIGINT",
    "float": "NUMERIC",
    "boolean": "BOOLEAN",
    "date": "DATE",
}

# Provenance columns added to every extracted table.
_PROVENANCE_COLUMNS: list[tuple[str, str]] = [
    ("source_page_numbers", "JSONB"),
    ("extraction_confidence", "NUMERIC"),
    ("reconciliation_notes", "TEXT"),
]

# Subset of Postgres reserved words we explicitly reject as identifiers.
# Identifiers are quoted in the DDL anyway, but rejecting these defends
# against confusing column names like `select` or `table`.
_RESERVED_WORDS: frozenset[str] = frozenset(
    {
        "all",
        "analyse",
        "analyze",
        "and",
        "any",
        "array",
        "as",
        "asc",
        "asymmetric",
        "both",
        "case",
        "cast",
        "check",
        "collate",
        "column",
        "constraint",
        "create",
        "current_catalog",
        "current_date",
        "current_role",
        "current_time",
        "current_timestamp",
        "current_user",
        "default",
        "deferrable",
        "desc",
        "distinct",
        "do",
        "else",
        "end",
        "except",
        "false",
        "fetch",
        "for",
        "foreign",
        "from",
        "grant",
        "group",
        "having",
        "in",
        "initially",
        "intersect",
        "into",
        "lateral",
        "leading",
        "limit",
        "localtime",
        "localtimestamp",
        "not",
        "null",
        "offset",
        "on",
        "only",
        "or",
        "order",
        "placing",
        "primary",
        "references",
        "returning",
        "select",
        "session_user",
        "some",
        "symmetric",
        "table",
        "then",
        "to",
        "trailing",
        "true",
        "union",
        "unique",
        "user",
        "using",
        "variadic",
        "when",
        "where",
        "window",
        "with",
    }
)

# Names that collide with the synthetic / provenance columns we add.
_RESERVED_INTERNAL: frozenset[str] = frozenset(
    {"id", "source_page_numbers", "extraction_confidence", "reconciliation_notes"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_snake_case(raw: str) -> str:
    """Lowercase, NFKC-strip, replace non-[a-z0-9_] runs with single underscores."""
    name = raw.strip().lower()
    name = re.sub(r"[^a-z0-9_]+", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip("_")
    return name


def _validate_identifier(raw: str, kind: str) -> str:
    """Normalize and validate a table/column identifier.

    Raises ValueError on empty, all-numeric, or reserved-word identifiers.
    """
    name = _to_snake_case(raw)
    if not name:
        raise ValueError(f"{kind} name is empty after normalization: {raw!r}")
    if name[0].isdigit():
        raise ValueError(f"{kind} name cannot start with a digit: {name!r}")
    if name in _RESERVED_WORDS:
        raise ValueError(f"{kind} name is a reserved word: {name!r}")
    return name


def _column_sql(col: ColumnDef) -> str:
    pg_type = _TYPE_MAP[col.type]
    return f'"{col.name}" {pg_type}'


# ---------------------------------------------------------------------------
# Validation pass — runs before DDL emission, returns a normalized model
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Result of validating a DatabaseModel against the DDL contract."""

    model: DatabaseModel
    notes: list[str] = field(default_factory=list)


def validate_model(model: DatabaseModel) -> ValidationResult:
    """Normalize identifiers and downgrade structurally invalid relationships.

    - Snake-cases all table and column names in place (returns a copy).
    - Rejects empty/duplicate/reserved identifiers with ValueError.
    - Downgrades any relationship whose `references_column` is not
      `is_primary_key=True` on the referenced table to `enabled=False`,
      and records a note. The downgraded relationship is preserved so
      reconciliation can still attempt soft FK resolution.
    """
    normalized = deepcopy(model)
    notes: list[str] = []

    # 1. Normalize and validate table + column identifiers.
    seen_table_names: set[str] = set()
    table_columns: dict[str, dict[str, ColumnDef]] = {}

    for table in normalized.tables:
        table.table_name = _validate_identifier(table.table_name, "table")
        if table.table_name in seen_table_names:
            raise ValueError(f"duplicate table name: {table.table_name!r}")
        seen_table_names.add(table.table_name)

        seen_col_names: set[str] = set()
        for col in table.columns:
            col.name = _validate_identifier(col.name, "column")
            if col.name in _RESERVED_INTERNAL:
                raise ValueError(
                    f"column name {col.name!r} on table {table.table_name!r} "
                    f"collides with a reserved provenance/synthetic column"
                )
            if col.name in seen_col_names:
                raise ValueError(
                    f"duplicate column {col.name!r} on table {table.table_name!r}"
                )
            seen_col_names.add(col.name)

        table_columns[table.table_name] = {c.name: c for c in table.columns}

    # 2. Validate relationships.
    for rel in normalized.relationships:
        rel.source_table = _to_snake_case(rel.source_table)
        rel.source_column = _to_snake_case(rel.source_column)
        rel.references_table = _to_snake_case(rel.references_table)
        rel.references_column = _to_snake_case(rel.references_column)
        if rel.composite_key_columns:
            rel.composite_key_columns = [
                _to_snake_case(c) for c in rel.composite_key_columns
            ]

        if rel.source_table not in table_columns:
            rel.enabled = False
            notes.append(
                f"relationship downgraded: source_table {rel.source_table!r} not in model"
            )
            continue
        if rel.references_table not in table_columns:
            rel.enabled = False
            notes.append(
                f"relationship downgraded: references_table {rel.references_table!r} not in model"
            )
            continue

        src_cols = table_columns[rel.source_table]
        ref_cols = table_columns[rel.references_table]

        if rel.source_column not in src_cols:
            rel.enabled = False
            notes.append(
                f"relationship downgraded: column {rel.source_table}.{rel.source_column} not declared"
            )
            continue
        if rel.references_column not in ref_cols:
            rel.enabled = False
            notes.append(
                f"relationship downgraded: column {rel.references_table}.{rel.references_column} not declared"
            )
            continue
        if not ref_cols[rel.references_column].is_primary_key:
            rel.enabled = False
            notes.append(
                f"relationship downgraded: {rel.references_table}.{rel.references_column} "
                f"is not is_primary_key=True (FKs must reference a unique natural key)"
            )

    return ValidationResult(model=normalized, notes=notes)


# ---------------------------------------------------------------------------
# DDL emission
# ---------------------------------------------------------------------------


def build_ddl(model: DatabaseModel, schema_name: str) -> list[str]:
    """Translate a DatabaseModel into ordered DDL statements.

    Order:
        1. CREATE TABLE for every TableDef
        2. ALTER TABLE ... ADD CONSTRAINT ... UNIQUE for is_primary_key columns
        3. ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY for enabled relationships

    The schema itself is created by the output provider (CREATE SCHEMA IF
    NOT EXISTS), not here, so this list contains only DDL that runs *inside*
    the target schema.
    """
    statements, _, _ = build_ddl_with_notes(model, schema_name)
    return statements


def _emit_ddl(normalized: DatabaseModel, schema_name: str) -> list[str]:
    """Emit DDL from an *already-validated* DatabaseModel."""
    statements: list[str] = []
    qschema = f'"{schema_name}"'

    # 1. CREATE TABLE
    for table in normalized.tables:
        cols_sql: list[str] = ['"id" BIGSERIAL PRIMARY KEY']
        for col in table.columns:
            cols_sql.append(_column_sql(col))
        for prov_name, prov_type in _PROVENANCE_COLUMNS:
            cols_sql.append(f'"{prov_name}" {prov_type}')

        body = ",\n    ".join(cols_sql)
        statements.append(
            f'CREATE TABLE {qschema}."{table.table_name}" (\n    {body}\n)'
        )

    # 2. UNIQUE constraints on primary-key columns (one per column).
    for table in normalized.tables:
        for col in table.columns:
            if col.is_primary_key:
                constraint = f"uq_{table.table_name}_{col.name}"
                statements.append(
                    f'ALTER TABLE {qschema}."{table.table_name}" '
                    f'ADD CONSTRAINT "{constraint}" UNIQUE ("{col.name}")'
                )

    # 3. FOREIGN KEY constraints for enabled relationships.
    for idx, rel in enumerate(normalized.relationships):
        if not rel.enabled:
            continue
        constraint = f"fk_{rel.source_table}_{rel.source_column}_{idx}"
        statements.append(
            f'ALTER TABLE {qschema}."{rel.source_table}" '
            f'ADD CONSTRAINT "{constraint}" '
            f'FOREIGN KEY ("{rel.source_column}") '
            f'REFERENCES {qschema}."{rel.references_table}" ("{rel.references_column}")'
        )

    return statements


def build_ddl_with_notes(
    model: DatabaseModel, schema_name: str
) -> tuple[list[str], DatabaseModel, list[str]]:
    """Validate, normalize, and emit DDL — returning all three artifacts.

    The provisioning task uses this to record any downgraded relationships
    in `target_ddl` audit / reconciliation_notes.
    """
    result = validate_model(model)
    statements = _emit_ddl(result.model, schema_name)
    return statements, result.model, result.notes
