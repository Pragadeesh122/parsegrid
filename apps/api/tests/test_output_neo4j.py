from __future__ import annotations

import pytest

from app.providers.output_neo4j import Neo4jOutputProvider
from app.schemas.extraction_model import (
    ColumnDef,
    DatabaseModel,
    RelationshipDef,
    TableDef,
)


class _FakeResult:
    def consume(self):
        return None


class _FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail = False

    def run(self, query: str, **params):
        if self.fail:
            raise RuntimeError("neo4j connection failed")
        self.calls.append((query, params))
        return _FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeDriver:
    def __init__(self, session: _FakeSession):
        self._session = session

    def session(self, database: str):
        assert database
        return self._session

    def close(self):
        return None


def _sample_model() -> DatabaseModel:
    return DatabaseModel(
        extraction_type="table_graph",
        tables=[
            TableDef(
                table_name="company",
                description="Companies",
                columns=[
                    ColumnDef(
                        name="company_id",
                        type="string",
                        description="PK",
                        is_primary_key=True,
                    ),
                    ColumnDef(
                        name="name",
                        type="string",
                        description="Name",
                        is_primary_key=False,
                    ),
                ],
            ),
            TableDef(
                table_name="invoice",
                description="Invoices",
                columns=[
                    ColumnDef(
                        name="invoice_id",
                        type="string",
                        description="PK",
                        is_primary_key=True,
                    ),
                    ColumnDef(
                        name="company_id",
                        type="string",
                        description="FK",
                        is_primary_key=False,
                    ),
                ],
            ),
        ],
        relationships=[
            RelationshipDef(
                source_table="invoice",
                source_column="company_id",
                references_table="company",
                references_column="company_id",
                link_basis="natural_key",
                nullable=True,
                enabled=True,
            )
        ],
    )


def test_neo4j_provision_materializes_nodes_and_edges(monkeypatch):
    fake_session = _FakeSession()
    provider = Neo4jOutputProvider()
    monkeypatch.setattr(
        provider,
        "_build_driver",
        lambda uri, user, password: _FakeDriver(fake_session),
    )

    model = _sample_model()
    data = {
        "company": [{"company_id": "acme", "name": "Acme Inc"}],
        "invoice": [{"invoice_id": "inv-1", "company_id": "acme"}],
    }

    result = provider.provision(
        schema_name="job_123",
        ddl_statements=[],
        data=data,
        model=model,
    )

    assert result.rows_inserted == 2
    assert result.schema_name == "job_123"
    assert "GRAPH PROVISION SUMMARY" in result.ddl_executed
    assert "scope=job_123" in result.ddl_executed

    queries = [q for q, _ in fake_session.calls]
    params = [p for _, p in fake_session.calls]

    assert any("MERGE (n:`company`" in q for q in queries)
    assert any("MERGE (n:`invoice`" in q for q in queries)
    assert any("MERGE (s)-[r:`INVOICE_TO_COMPANY`" in q for q in queries)
    assert all(p.get("scope") == "job_123" for p in params if "scope" in p)


def test_neo4j_test_connection_raises_on_failure(monkeypatch):
    fake_session = _FakeSession()
    fake_session.fail = True
    provider = Neo4jOutputProvider()
    monkeypatch.setattr(
        provider,
        "_build_driver",
        lambda uri, user, password: _FakeDriver(fake_session),
    )

    with pytest.raises(RuntimeError):
        provider.test_connection("bolt://neo4j:parsegrid@localhost:7687/neo4j")


def test_neo4j_delete_output_scoped(monkeypatch):
    fake_session = _FakeSession()
    provider = Neo4jOutputProvider()
    monkeypatch.setattr(
        provider,
        "_build_driver",
        lambda uri, user, password: _FakeDriver(fake_session),
    )

    provider.delete_output("job_789")

    assert any("DETACH DELETE" in q for q, _ in fake_session.calls)
    delete_calls = [(q, p) for q, p in fake_session.calls if "DETACH DELETE" in q]
    assert delete_calls and delete_calls[0][1]["scope"] == "job_789"

