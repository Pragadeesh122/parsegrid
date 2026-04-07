"""ParseGrid — Database provisioning service (Phase 7).

Thin wrapper that delegates to the configured output provider. Phase 7
passes the locked DatabaseModel and a `dict[table_name, rows]` payload
through unchanged — the provider does FK-aware ordering and inserts.
"""

from __future__ import annotations

from typing import Any

from app.providers import ProvisionResult
from app.providers.factory import get_output_provider
from app.schemas.extraction_model import DatabaseModel


def provision_and_insert(
    schema_name: str,
    ddl_statements: list[str],
    data: dict[str, list[dict[str, Any]]],
    model: DatabaseModel,
    output_format: str = "SQL",
) -> ProvisionResult:
    """Create schema, run DDL, and insert rows in FK dependency order."""
    provider = get_output_provider(output_format)
    return provider.provision(
        schema_name=schema_name,
        ddl_statements=ddl_statements,
        data=data,
        model=model,
    )
