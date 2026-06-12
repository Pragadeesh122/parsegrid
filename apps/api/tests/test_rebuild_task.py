# apps/api/tests/test_rebuild_task.py
"""Tier 2: rebuild_dataset unions document buckets and dispatches translate."""

import json

import pytest

from app.worker.tasks import reconcile as rebuild_task
from tests.factories import make_column, make_model, make_table

MODEL = make_model([make_table("invoices", [make_column("invoice_number", pk=True)])])


def _buckets(doc: str, numbers: list[str]) -> dict:
    return {
        "tables": {
            "invoices": {
                "rows": [{"invoice_number": n, "__chunk_index": f"{doc}:0"} for n in numbers],
                "chunk_pages": {f"{doc}:0": [1]},
            }
        }
    }


class _BoomOpenAI:
    """Stand-in for openai.OpenAI whose construction raises.

    Forces entity_resolution's documented fallback path (rows returned
    unchanged) without any real network call, so canonicalize_parents
    performs the PK dedupe deterministically.
    """

    def __init__(self, *args, **kwargs):
        raise RuntimeError("no network in tests")


@pytest.fixture
def wired(monkeypatch):
    state = {"job_updates": [], "dispatched": [], "uploads": []}
    monkeypatch.setattr(
        rebuild_task,
        "get_job_field",
        lambda job_id, *c: {"locked_model": MODEL.model_dump()},
    )
    monkeypatch.setattr(
        rebuild_task, "update_job", lambda job_id, **kw: state["job_updates"].append(kw)
    )
    monkeypatch.setattr(rebuild_task, "publish_status", lambda *a, **kw: None)
    monkeypatch.setattr(
        "app.core.storage.upload_file_to_s3",
        lambda file_bytes, object_key, content_type: state["uploads"].append(object_key),
    )
    monkeypatch.setattr(
        "app.worker.tasks.translate.translate_and_provision",
        type("T", (), {"apply_async": staticmethod(lambda args: state["dispatched"].append(args))}),
    )
    monkeypatch.setattr("openai.OpenAI", _BoomOpenAI)
    return state


def test_unions_documents_and_dedupes_across_files(wired, monkeypatch):
    docs = [
        {"id": "d1", "status": "EXTRACTED", "extracted_buckets": _buckets("d1", ["1", "2"])},
        {"id": "d2", "status": "EXTRACTED", "extracted_buckets": _buckets("d2", ["2", "3"])},
        {"id": "d3", "status": "REJECTED", "extracted_buckets": _buckets("d3", ["9"])},
    ]
    monkeypatch.setattr(rebuild_task, "list_job_documents", lambda job_id, *c: docs)

    rebuild_task.rebuild_dataset.run("job-1")

    final = next(u for u in wired["job_updates"] if "extracted_data" in u)
    data = json.loads(final["extracted_data"])
    numbers = sorted(r["invoice_number"] for r in data["invoices"])
    assert numbers == ["1", "2", "3"]  # "2" deduped across documents; d3 excluded
    assert wired["dispatched"] == [["job-1"]]


def test_legacy_document_adopts_job_level_s3_buckets(wired, monkeypatch):
    docs = [{"id": "d1", "status": "EXTRACTED", "extracted_buckets": None}]
    monkeypatch.setattr(rebuild_task, "list_job_documents", lambda job_id, *c: docs)
    adopted = []
    monkeypatch.setattr(
        rebuild_task, "update_document", lambda doc_id, **kw: adopted.append(doc_id)
    )

    import io

    legacy = _buckets("0", ["7"])

    class _FakeS3:
        def get_object(self, Bucket, Key):  # noqa: N803
            assert Key == "extracted/job-1/merged_buckets.json"
            return {"Body": io.BytesIO(json.dumps(legacy).encode())}

    monkeypatch.setattr("app.core.storage.get_s3_client", lambda: _FakeS3())

    rebuild_task.rebuild_dataset.run("job-1")

    assert adopted == ["d1"]
    final = next(u for u in wired["job_updates"] if "extracted_data" in u)
    assert json.loads(final["extracted_data"])["invoices"][0]["invoice_number"] == "7"


def test_no_extracted_documents_fails_loud(wired, monkeypatch):
    monkeypatch.setattr(rebuild_task, "list_job_documents", lambda job_id, *c: [])
    with pytest.raises(ValueError):
        rebuild_task.rebuild_dataset.run("job-1")
    assert any(kw.get("status") == "FAILED" for kw in wired["job_updates"])
