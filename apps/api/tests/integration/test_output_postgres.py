"""Tier 3: PostgreSQL output provider — idempotent (rebuild-safe) provisioning.

Re-provisioning the same schema (what a dataset rebuild does after an append)
must drop and recreate it, not fail on pre-existing tables. Exercises the real
provider against the test Postgres.
"""

import pytest
from sqlalchemy import create_engine, text

from app.providers.output_postgres import PostgresOutputProvider
from app.services.ddl import build_ddl
from tests.factories import make_column, make_model, make_table
from tests.integration.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration

SCHEMA = "job_provision_idempotency_test"
MODEL = make_model(
    [
        make_table(
            "invoices",
            [make_column("invoice_number", pk=True), make_column("total", "float")],
        )
    ]
)


def _sync_url() -> str:
    return TEST_DATABASE_URL.replace("+asyncpg", "")


@pytest.fixture
def provider(database_available, monkeypatch):
    # The provider builds its own engine from settings.database_url — point it
    # at the test database for the duration of the test.
    monkeypatch.setattr(
        "app.providers.output_postgres.settings.database_url", TEST_DATABASE_URL, raising=True
    )
    p = PostgresOutputProvider()
    yield p
    p.delete_output(SCHEMA)


def _provision(provider, rows):
    data = {"invoices": rows}
    return provider.provision(
        schema_name=SCHEMA,
        ddl_statements=build_ddl(MODEL, SCHEMA),
        data=data,
        model=MODEL,
    )


def test_reprovision_same_schema_succeeds_and_reflects_latest_data(provider):
    first = _provision(provider, [{"invoice_number": "INV-1", "total": 10.0}])
    assert first.rows_inserted == 1

    # Rebuild after an append: same schema, a superset of rows. Must not fail
    # on the existing table and must reflect the new dataset, not duplicates.
    second = _provision(
        provider,
        [
            {"invoice_number": "INV-1", "total": 10.0},
            {"invoice_number": "INV-2", "total": 20.0},
        ],
    )
    assert second.rows_inserted == 2

    engine = create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            count = conn.execute(text(f'SELECT count(*) FROM "{SCHEMA}".invoices')).scalar()
    finally:
        engine.dispose()
    assert count == 2  # rebuilt from scratch — no leftover/duplicated rows
