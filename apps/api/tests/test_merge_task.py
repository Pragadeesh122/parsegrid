# apps/api/tests/test_merge_task.py
"""Tier 2: merge buckets per document; append compatibility gate routing."""

import json

import pytest

from app.worker.tasks import merge as merge_task
from tests.factories import make_column, make_model, make_table

MODEL = make_model([make_table("invoices", [make_column("invoice_number", pk=True)])])


def _chunk_result(doc: str, key: str, rows: list[dict]) -> dict:
    return {
        "document_id": doc,
        "table_name": "invoices",
        "chunk_key": key,
        "rows": rows,
        "pages": [1],
        "tokens": {},
    }


@pytest.fixture
def wired(monkeypatch):
    state = {"doc_updates": [], "job_updates": [], "rebuilds": [], "published": []}
    monkeypatch.setattr(
        merge_task,
        "update_document",
        lambda doc_id, **kw: state["doc_updates"].append((doc_id, kw)),
    )
    monkeypatch.setattr(
        merge_task,
        "update_job",
        lambda job_id, **kw: state["job_updates"].append(kw),
    )
    monkeypatch.setattr(
        merge_task,
        "publish_status",
        lambda job_id, status, progress, **kw: state["published"].append(status),
    )
    monkeypatch.setattr(
        merge_task,
        "get_job_field",
        lambda job_id, *c: {
            "locked_model": MODEL.model_dump(),
            "document_profile": {"region_summary": {"text": 10}},
        },
    )
    monkeypatch.setattr(merge_task, "_document_histogram", lambda job_id, doc_id: None)
    monkeypatch.setattr(
        "app.worker.tasks.reconcile.rebuild_dataset",
        type("T", (), {"apply_async": staticmethod(lambda args: state["rebuilds"].append(args))}),
    )
    return state


def test_creation_merge_stores_buckets_per_document_and_rebuilds(wired):
    results = [
        _chunk_result("d1", "d1:0", [{"invoice_number": "1"}]),
        _chunk_result("d2", "d2:0", [{"invoice_number": "2"}]),
    ]
    merge_task.merge_results.run(results, "job-1")

    stored = {doc_id: kw for doc_id, kw in wired["doc_updates"]}
    assert set(stored) == {"d1", "d2"}
    d1 = json.loads(stored["d1"]["extracted_buckets"])
    assert d1["tables"]["invoices"]["rows"][0]["__chunk_index"] == "d1:0"
    assert d1["tables"]["invoices"]["chunk_pages"] == {"d1:0": [1]}
    assert wired["rebuilds"] == [["job-1"]]


def test_compatible_append_auto_rebuilds(wired):
    results = [_chunk_result("d9", "d9:0", [{"invoice_number": "5"}])]
    merge_task.merge_results.run(results, "job-1", "d9")

    reports = [
        json.loads(kw["compat_report"])
        for doc_id, kw in wired["doc_updates"]
        if "compat_report" in kw
    ]
    assert reports[0]["needs_review"] is False
    assert wired["rebuilds"] == [["job-1"]]
    assert "AWAITING_APPEND_REVIEW" not in wired["published"]


def test_incompatible_append_pauses_for_review(wired):
    results = [_chunk_result("d9", "d9:0", [])]  # zero rows extracted
    merge_task.merge_results.run(results, "job-1", "d9")

    assert wired["rebuilds"] == []
    assert "AWAITING_APPEND_REVIEW" in wired["published"]
    assert any(kw.get("status") == "AWAITING_APPEND_REVIEW" for kw in wired["job_updates"])
