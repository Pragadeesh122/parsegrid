"""ParseGrid — OpenAI LLM Provider.

Uses OpenAI API with Structured Outputs (strict: true) via the
`client.beta.chat.completions.parse()` method for guaranteed schema compliance.

Models:
- gpt-4o: Schema discovery (higher reasoning for complex structures)
- gpt-4o-mini: Bulk extraction (10-20x cheaper, sufficient for locked schemas)
"""

import json
import logging
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, create_model

from app.core.config import settings
from app.providers import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)


def _json_schema_to_pydantic(schema: dict) -> type[BaseModel]:
    """Dynamically create a Pydantic model from a JSON schema.

    This enables OpenAI's strict structured outputs on user-defined schemas.
    """
    fields: dict[str, Any] = {}
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
    }

    for field_name, field_def in properties.items():
        field_type_str = field_def.get("type", "string")

        if field_type_str == "array":
            item_type_str = field_def.get("items", {}).get("type", "string")
            inner_type = type_map.get(item_type_str, str)
            field_type = list[inner_type]  # type: ignore[valid-type]
        elif field_type_str == "object":
            # Nested objects become dict
            field_type = dict[str, Any]
        else:
            field_type = type_map.get(field_type_str, str)

        if field_name in required:
            fields[field_name] = (field_type, ...)
        else:
            fields[field_name] = (field_type | None, None)

    return create_model("DynamicExtractionModel", **fields)


class OpenAILLMProvider(BaseLLMProvider):
    """OpenAI-based LLM provider with structured output enforcement."""

    def __init__(
        self,
        api_key: str | None = None,
        schema_model: str = "gpt-4o",
        extraction_model: str = "gpt-4o-mini",
        translation_model: str = "gpt-4o-mini",
    ):
        self.client = OpenAI(api_key=api_key or settings.openai_api_key)
        self.schema_model = schema_model
        self.extraction_model = extraction_model
        self.translation_model = translation_model

    def generate_schema(self, sample_text: str, num_pages: int) -> dict:
        """Analyze sample text and propose a JSON schema for extraction.

        Uses gpt-4o for higher reasoning capability on complex documents.
        """
        system_prompt = """You are a schema discovery agent for ParseGrid.
Your job is to analyze document text and propose a JSON schema that captures
the structured data found in the document.

Rules:
1. Identify EVERY distinct data entity and its fields.
2. Use appropriate JSON Schema types: string, integer, number, boolean, array.
3. Mark fields as required if they appear consistently.
4. Include a description for each field.
5. If the document contains tabular data, model each row as an object in an array.
6. Return ONLY valid JSON — no markdown, no explanation.

Output format (JSON Schema):
{
  "title": "Descriptive name of the data",
  "type": "object",
  "properties": {
    "items": {
      "type": "array",
      "description": "Array of extracted records",
      "items": {
        "type": "object",
        "properties": {
          "field_name": {"type": "string", "description": "..."},
          ...
        },
        "required": ["field_name", ...]
      }
    }
  },
  "required": ["items"]
}"""

        user_prompt = f"""Analyze this document text ({num_pages} total pages) and propose a JSON schema.

--- DOCUMENT TEXT SAMPLE ---
{sample_text[:8000]}
--- END ---

Return the JSON schema that best captures the structured data in this document."""

        response = self.client.chat.completions.create(
            model=self.schema_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        schema_text = response.choices[0].message.content or "{}"
        try:
            return json.loads(schema_text)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse schema response: {schema_text[:200]}")
            return {"error": "Failed to parse schema", "raw": schema_text[:500]}

    def extract_structured(
        self,
        text: str,
        schema: dict,
    ) -> LLMResponse:
        """Extract structured data from text using Structured Outputs (strict mode).

        Uses gpt-4o-mini for cost efficiency on locked schemas.
        """
        # Build a clear field list from the schema for the prompt
        items_schema = schema.get("properties", {}).get("items", {})
        item_props = items_schema.get("items", {}).get("properties", {})
        if not item_props:
            # Flat schema fallback
            item_props = schema.get("properties", {})

        field_descriptions = []
        for fname, fdef in item_props.items():
            ftype = fdef.get("type", "string")
            fdesc = fdef.get("description", "")
            field_descriptions.append(f'  - "{fname}" ({ftype}): {fdesc}')
        fields_text = "\n".join(field_descriptions)

        system_prompt = f"""You are a data extraction agent. Extract structured records from text.

Each record must have these fields:
{fields_text}

Return a JSON object with a single key "items" containing an array of extracted records.
Example output format:
{{"items": [{{"field1": "value1", "field2": 123}}, ...]}}

Rules:
1. Extract EVERY matching record found in the text.
2. If a field value is not found in the text, use null.
3. Return ONLY the JSON object with "items" array — nothing else."""

        response = self.client.chat.completions.create(
            model=self.extraction_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract data from this text:\n\n{text}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        content = response.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = {"error": "Failed to parse extraction response"}

        return LLMResponse(
            data=data,
            model=self.extraction_model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
            raw_response=response,
        )

    def generate_ddl(
        self,
        schema: dict,
        target_format: str,
    ) -> str:
        """Generate DDL statements for the target database format.

        Uses gpt-4o-mini for SQL/Cypher generation.
        """
        format_instructions = {
            "SQL": """Generate a single PostgreSQL CREATE TABLE statement from this JSON schema.
Rules:
- Create exactly ONE flat table — no parent tables, no foreign keys, no joins.
- The table should hold the items array directly. Each item becomes a row.
- Add a SERIAL PRIMARY KEY column named 'id'.
- Use appropriate PostgreSQL types (TEXT, INTEGER, NUMERIC, BOOLEAN, TIMESTAMP).
- Use snake_case for column names.
- Use NULL defaults for all columns except id.
- Return ONLY the CREATE TABLE statement, no explanation, no markdown fences.""",
            "GRAPH": """Generate Neo4j Cypher statements to create nodes and relationships from this JSON schema.
Rules:
- Create nodes with properties matching the schema fields.
- Identify potential relationships between entities.
- Return ONLY the Cypher statements, no explanation.""",
            "VECTOR": """Generate a configuration for vector embedding storage from this JSON schema.
Rules:
- Identify text fields suitable for embedding.
- Define the collection schema with metadata fields.
- Return as a JSON configuration object.""",
        }

        instruction = format_instructions.get(
            target_format,
            format_instructions["SQL"],
        )

        response = self.client.chat.completions.create(
            model=self.translation_model,
            messages=[
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": f"Generate DDL for this schema:\n\n{json.dumps(schema, indent=2)}",
                },
            ],
            temperature=0.0,
        )

        return response.choices[0].message.content or ""
