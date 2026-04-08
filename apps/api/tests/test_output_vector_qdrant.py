from __future__ import annotations

import pytest

from app.providers.output_vector_qdrant import QdrantOutputProvider
from app.schemas.extraction_model import ColumnDef, DatabaseModel, TableDef


class _FakeEmbedder:
    dimension = 4

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class _FakeQdrantClient:
    def __init__(self):
        self.collections: set[str] = set()
        self.created: list[tuple[str, object]] = []
        self.deleted: list[str] = []
        self.upserted: list[tuple[str, list[object]]] = []
        self.fail_connect = False

    def get_collections(self):
        if self.fail_connect:
            raise RuntimeError("qdrant unavailable")
        return {"collections": list(self.collections)}

    def get_collection(self, collection_name: str):
        if collection_name not in self.collections:
            raise RuntimeError("not found")
        return {"name": collection_name}

    def create_collection(self, collection_name: str, vectors_config):
        self.collections.add(collection_name)
        self.created.append((collection_name, vectors_config))

    def delete_collection(self, collection_name: str):
        self.collections.discard(collection_name)
        self.deleted.append(collection_name)

    def upsert(self, collection_name: str, points, wait: bool):
        assert wait is True
        self.upserted.append((collection_name, list(points)))


def _sample_model() -> DatabaseModel:
    return DatabaseModel(
        extraction_type="single_table",
        tables=[
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
                        name="total",
                        type="float",
                        description="Total",
                        is_primary_key=False,
                    ),
                ],
            )
        ],
        relationships=[],
    )


def test_qdrant_provision_creates_collection_and_points(monkeypatch):
    fake_client = _FakeQdrantClient()
    provider = QdrantOutputProvider()

    monkeypatch.setattr(provider, "_build_client", lambda url, api_key: fake_client)
    monkeypatch.setattr(
        "app.providers.output_vector_qdrant.get_embedding_provider",
        lambda: _FakeEmbedder(),
    )

    data = {
        "invoice": [
            {"invoice_id": "inv-1", "total": 10.5},
            {"invoice_id": "inv-2", "total": 25.0},
        ]
    }

    result = provider.provision(
        schema_name="job_456",
        ddl_statements=[],
        data=data,
        model=_sample_model(),
    )

    assert result.rows_inserted == 2
    assert result.schema_name == "job_456"
    assert "VECTOR PROVISION SUMMARY" in result.ddl_executed
    assert result.connection_string.endswith("/collections/job_456")

    assert fake_client.created and fake_client.created[0][0] == "job_456"
    assert fake_client.upserted and fake_client.upserted[0][0] == "job_456"
    assert len(fake_client.upserted[0][1]) == 2


def test_qdrant_delete_output_scoped(monkeypatch):
    fake_client = _FakeQdrantClient()
    fake_client.collections.add("job_abc")
    provider = QdrantOutputProvider()

    monkeypatch.setattr(provider, "_build_client", lambda url, api_key: fake_client)

    provider.delete_output("job_abc")

    assert "job_abc" in fake_client.deleted


def test_qdrant_test_connection_raises_on_failure(monkeypatch):
    fake_client = _FakeQdrantClient()
    fake_client.fail_connect = True
    provider = QdrantOutputProvider()

    monkeypatch.setattr(provider, "_build_client", lambda url, api_key: fake_client)

    with pytest.raises(RuntimeError):
        provider.test_connection("http://localhost:6333")

