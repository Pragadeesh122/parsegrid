"""Shared builders for extraction-model objects used across the test suite."""

from __future__ import annotations

from app.schemas.extraction_model import (
    ColumnDef,
    DatabaseModel,
    RelationshipDef,
    TableDef,
)


def make_column(name: str, col_type: str = "string", pk: bool = False) -> ColumnDef:
    return ColumnDef(name=name, type=col_type, is_primary_key=pk)


def make_table(name: str, columns: list[ColumnDef], description: str = "") -> TableDef:
    return TableDef(table_name=name, description=description, columns=columns)


def make_rel(
    source_table: str,
    source_column: str,
    references_table: str,
    references_column: str,
    **kwargs,
) -> RelationshipDef:
    kwargs.setdefault("link_basis", "natural_key")
    return RelationshipDef(
        source_table=source_table,
        source_column=source_column,
        references_table=references_table,
        references_column=references_column,
        **kwargs,
    )


def make_model(
    tables: list[TableDef],
    relationships: list[RelationshipDef] | None = None,
    extraction_type: str = "table_graph",
) -> DatabaseModel:
    return DatabaseModel(
        extraction_type=extraction_type,
        tables=tables,
        relationships=relationships or [],
    )
