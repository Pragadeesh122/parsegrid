"""ParseGrid — Database provisioning service.

Delegates to the appropriate output provider based on the job's output_format.
This module is the entry point called by the translate Celery task.
"""

from app.providers import ProvisionResult
from app.providers.factory import get_output_provider


def provision_and_insert(
    schema_name: str,
    ddl_statements: str,
    data: dict | list,
    json_schema: dict,
    output_format: str = "SQL",
) -> ProvisionResult:
    """Create schema, execute DDL, bulk insert data via the output provider.

    Args:
        schema_name: Isolated schema/namespace name (e.g., job_{uuid}).
        ddl_statements: DDL string from the Translator Agent.
        data: The merged extraction data.
        json_schema: The locked JSON schema (for table name inference).
        output_format: "SQL", "GRAPH", or "VECTOR".

    Returns:
        ProvisionResult with connection_string, rows_inserted, schema_name, ddl_executed.
    """
    provider = get_output_provider(output_format)
    return provider.provision(schema_name, ddl_statements, data, json_schema)
