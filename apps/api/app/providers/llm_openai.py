"""ParseGrid — OpenAI LLM Provider (Phase 7).

Two responsibilities:

1. `generate_model` — discover a typed `DatabaseModel` from sampled document
   text using `client.beta.chat.completions.parse(response_format=DatabaseModel)`.
   The Pydantic schema is enforced server-side by OpenAI strict structured
   outputs, so the response cannot contain arbitrary JSON keys.

2. `extract_table` — extract rows for a single `TableDef` from a chunk of
   text. A per-table Pydantic model is built dynamically at request time so
   each call is also strict-mode.

DDL generation has been removed entirely (Phase 7) — see `app/services/ddl.py`
for the deterministic Python generator that replaces it.
"""

import logging
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, create_model

from app.core.config import settings
from app.providers import BaseLLMProvider, LLMResponse
from app.schemas.extraction_model import (
    ColumnDef,
    DatabaseModel,
    DocumentProfile,
    RelationshipDef,
    TableDef,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-table dynamic Pydantic models
# ---------------------------------------------------------------------------

_PY_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "float": float,
    "boolean": bool,
    "date": str,  # ISO 8601 string; reconciliation normalizes to a real date
}


def _table_def_to_pydantic(table: TableDef, link_targets: list[RelationshipDef]) -> type[BaseModel]:
    """Build a strict Pydantic model for a single TableDef.

    The resulting model has one field per declared column, plus one extra
    nullable string field for every link target's `source_column`. The
    link-key fields hold the *natural* key value of the referenced parent —
    never a synthetic id — so reconciliation can resolve FKs deterministically.
    """
    fields: dict[str, Any] = {}

    for col in table.columns:
        py_type = _PY_TYPE_MAP.get(col.type, str)
        # All extracted fields are nullable: missing data should be null,
        # not a refusal or fabricated value.
        fields[col.name] = (py_type | None, Field(default=None, description=col.description))

    declared_names = {c.name for c in table.columns}
    for rel in link_targets:
        if not rel.enabled:
            continue
        if rel.source_column in declared_names:
            # Column already declared on the table; no extra link field needed.
            continue
        fields[rel.source_column] = (
            str | None,
            Field(
                default=None,
                description=(
                    f"Foreign key value linking to {rel.references_table}."
                    f"{rel.references_column} (natural key, never an id)."
                ),
            ),
        )

    row_model = create_model(
        f"Row_{table.table_name}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )

    wrapper = create_model(
        f"Extract_{table.table_name}",
        __config__=ConfigDict(extra="forbid"),
        rows=(list[row_model], ...),
    )
    return wrapper


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OpenAILLMProvider(BaseLLMProvider):
    """OpenAI-based LLM provider with strict structured-output enforcement."""

    def __init__(
        self,
        api_key: str | None = None,
        discovery_model: str = "gpt-5.4",
        extraction_model: str = "gpt-5.4",
    ):
        self.client = OpenAI(api_key=api_key or settings.openai_api_key)
        self.discovery_model = discovery_model
        self.extraction_model = extraction_model

    # ------------------------------------------------------------------
    # 1. Model discovery
    # ------------------------------------------------------------------

    def generate_model(
        self,
        document_text: str,
        profile: DocumentProfile | None,
        num_pages: int,
    ) -> DatabaseModel:
        """Propose a typed DatabaseModel from sampled document text.

        Temperature 0.1, gpt-5.4 by default. The response is parsed against
        DatabaseModel via OpenAI strict structured outputs, so the return
        value is guaranteed to validate.
        """
        system_prompt = """You are a relational data modeller for ParseGrid.

You will receive sampled text from a document. Your job is to propose a
DatabaseModel that captures the structured data found in the document. The
model has two shapes:

- single_table: ONE table that holds repeated records of the same entity
  (e.g., a batch of identical invoices, a list of transactions).
- table_graph: MULTIPLE tables linked by foreign keys when the document
  contains genuinely distinct entities (e.g., a mortgage application with
  borrowers, properties, employers, transactions).

Hard rules:
1. Pick `single_table` whenever the document is one repeated entity. Do NOT
   invent extra tables in that case.
2. Only create a table if you can point to multiple instances of that entity
   in the sampled text. Do NOT invent tables with no evidence.
3. Use snake_case for every table_name and column name.
4. Mark exactly the columns that uniquely identify a record as
   `is_primary_key=true`. These become UNIQUE constraints and FK targets.
5. For every relationship, the `references_column` MUST be a column that you
   marked `is_primary_key=true` on the referenced table. FKs reference the
   user's natural key, never a synthetic id.
6. `link_basis` is "natural_key" for single-column FKs, "composite_key" when
   multiple columns are needed (use composite_key_columns), or "manual_only"
   when the link is implied but not directly extractable.
7. Every column type must be one of: string, integer, float, boolean, date.
8. Provide a short, useful description for every table and every column.
"""

        profile_block = ""
        if profile is not None:
            profile_block = (
                f"\n--- DOCUMENT PROFILE ---\n"
                f"Total pages: {profile.total_pages}\n"
                f"Sampled pages: {profile.sampled_pages}\n"
                f"Region histogram: {profile.region_summary}\n"
                f"Recommended extraction_type: {profile.recommended_extraction_type}\n"
                f"Rationale: {profile.rationale}\n"
                f"--- END PROFILE ---\n"
            )

        user_prompt = (
            f"Propose a DatabaseModel for this document ({num_pages} pages total)."
            f"{profile_block}"
            f"\n\n--- SAMPLED DOCUMENT TEXT ---\n"
            f"{document_text[:16000]}"
            f"\n--- END ---"
        )

        completion = self.client.beta.chat.completions.parse(
            model=self.discovery_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=DatabaseModel,
            temperature=0.1,
        )

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            refusal = completion.choices[0].message.refusal or "no parsed output"
            raise RuntimeError(f"LLM refused or returned null DatabaseModel: {refusal}")

        logger.info(
            f"generate_model: extraction_type={parsed.extraction_type} "
            f"tables={[t.table_name for t in parsed.tables]} "
            f"relationships={len(parsed.relationships)}"
        )
        return parsed

    # ------------------------------------------------------------------
    # 2. Per-table extraction
    # ------------------------------------------------------------------

    def extract_table(
        self,
        text: str,
        table: TableDef,
        link_targets: list[RelationshipDef],
    ) -> LLMResponse:
        """Extract rows for a single table from one chunk of text."""
        wrapper_model = _table_def_to_pydantic(table, link_targets)

        # Build a human-readable column list for the prompt.
        col_lines: list[str] = []
        for col in table.columns:
            pk_marker = " [PRIMARY KEY]" if col.is_primary_key else ""
            col_lines.append(f'- "{col.name}" ({col.type}){pk_marker}: {col.description}')

        link_lines: list[str] = []
        for rel in link_targets:
            if not rel.enabled:
                continue
            link_lines.append(
                f'- "{rel.source_column}" → natural-key value of '
                f"{rel.references_table}.{rel.references_column}"
            )

        link_block = ""
        if link_lines:
            link_block = (
                "\n\nThis table also has foreign-key columns. For each row, "
                "extract the natural-key value of the parent record (e.g., the "
                "borrower_id, the invoice_number) — never a synthetic id.\n" + "\n".join(link_lines)
            )

        system_prompt = (
            f"You are a data extraction agent for ParseGrid.\n\n"
            f"Extract every record matching the table `{table.table_name}` "
            f"({table.description}) from the text below.\n\n"
            f"Each row must have these fields:\n"
            + "\n".join(col_lines)
            + link_block
            + "\n\nRules:\n"
            "1. Extract EVERY matching record found in the text.\n"
            "2. If a field value is not present in the text, set it to null. "
            "Never fabricate or guess.\n"
            "3. Return the rows in the order they appear in the text.\n"
            "4. Only extract data that belongs to this table — ignore unrelated content."
        )

        completion = self.client.beta.chat.completions.parse(
            model=self.extraction_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract rows from this text:\n\n{text}"},
            ],
            response_format=wrapper_model,
            temperature=0.0,
        )

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            refusal = completion.choices[0].message.refusal or "no parsed output"
            logger.warning(f"extract_table({table.table_name}): refusal/null parsed: {refusal}")
            data: dict[str, Any] = {"rows": []}
        else:
            data = parsed.model_dump()

        usage = completion.usage
        return LLMResponse(
            data=data,
            model=self.extraction_model,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            raw_response=completion,
        )
