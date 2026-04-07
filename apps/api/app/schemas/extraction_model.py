"""ParseGrid — Adaptive extraction meta-schema (Phase 7).

Fixed Pydantic contract used by:
- LLM discovery via OpenAI strict structured outputs
  (`client.beta.chat.completions.parse(response_format=DatabaseModel)`)
- Server-side validation in the approve-model endpoint
- Deterministic DDL generation in `app/services/ddl.py`
- Per-table extraction (`extract_table`)
- Reconciliation and FK resolution

The LLM cannot emit arbitrary JSON keys — it must conform to this schema.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ColumnType = Literal["string", "integer", "float", "boolean", "date"]
ExtractionType = Literal["single_table", "table_graph"]
LinkBasis = Literal["natural_key", "composite_key", "manual_only"]


class ColumnDef(BaseModel):
    """A single column on a TableDef."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="snake_case column name")
    type: ColumnType
    description: str = ""
    is_primary_key: bool = Field(
        default=False,
        description="When True, becomes a UNIQUE constraint and the target of foreign keys.",
    )


class RelationshipDef(BaseModel):
    """A foreign key relationship between two tables in the model."""

    model_config = ConfigDict(extra="forbid")

    source_table: str
    source_column: str
    references_table: str
    references_column: str = Field(
        ...,
        description="Must exist on references_table and be is_primary_key=True.",
    )
    link_basis: LinkBasis
    composite_key_columns: list[str] | None = None
    nullable: bool = True
    enabled: bool = True


class TableDef(BaseModel):
    """A single table in the extraction model."""

    model_config = ConfigDict(extra="forbid")

    table_name: str = Field(..., description="snake_case enforced server-side")
    description: str
    columns: list[ColumnDef]


class DatabaseModel(BaseModel):
    """The full extraction model — single table or relational graph."""

    model_config = ConfigDict(extra="forbid")

    extraction_type: ExtractionType
    tables: list[TableDef]
    relationships: list[RelationshipDef] = Field(default_factory=list)


class SectionCandidate(BaseModel):
    """A profiled section of the document and the tables it feeds."""

    model_config = ConfigDict(extra="forbid")

    section_id: str
    title: str
    page_range: tuple[int, int]
    assigned_tables: list[str] = Field(
        ...,
        description="table_names from DatabaseModel.tables. Empty list = unassigned (cover pages, appendices).",
    )


class DocumentProfile(BaseModel):
    """Whole-document profile produced before model proposal."""

    model_config = ConfigDict(extra="forbid")

    total_pages: int
    sampled_pages: list[int]
    region_summary: dict[str, int] = Field(
        default_factory=dict,
        description="Histogram of OCR region_type across the whole document.",
    )
    sections: list[SectionCandidate] = Field(default_factory=list)
    recommended_extraction_type: ExtractionType
    rationale: str = ""
