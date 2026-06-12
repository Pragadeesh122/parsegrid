# apps/api/tests/test_profile_task.py
"""Tier 2: multi-document profiling task (faked S3/LLM/DB)."""

import io
import json

import pytest

from app.worker.tasks import profile as profile_task
from tests.factories import make_column, make_model, make_table


def _ocr_json(pages: int) -> dict:
    return {
        "page_count": pages,
        "pages": [
            {"page_number": n, "regions": [{"region_type": "text", "text": f"p{n}"}]}
            for n in range(1, pages + 1)
        ],
    }


@pytest.fixture
def wired(monkeypatch):
    state = {"job_updates": [], "model_calls": []}
    docs = [
        {"id": "doc-a", "filename": "a.pdf", "page_count": 4, "status": "OCR_DONE"},
        {"id": "doc-b", "filename": "b.pdf", "page_count": 20, "status": "OCR_DONE"},
    ]
    monkeypatch.setattr(profile_task, "list_job_documents", lambda job_id, *c: docs)
    monkeypatch.setattr(
        profile_task, "update_job", lambda job_id, **kw: state["job_updates"].append(kw)
    )
    monkeypatch.setattr(profile_task, "publish_status", lambda *a, **kw: None)

    ocr_payloads = {"doc-a": _ocr_json(4), "doc-b": _ocr_json(20)}

    class _FakeS3:
        def get_object(self, Bucket, Key):  # noqa: N803
            doc_id = Key.split("/")[2]
            body = json.dumps(ocr_payloads[doc_id]).encode()
            return {"Body": io.BytesIO(body)}

    monkeypatch.setattr("app.core.storage.get_s3_client", lambda: _FakeS3())

    proposed = make_model([make_table("t", [make_column("a", pk=True)])])

    class _FakeLLM:
        def generate_model(self, document_text, profile, num_pages):
            state["model_calls"].append({"text": document_text, "pages": num_pages})
            return proposed

    monkeypatch.setattr("app.providers.factory.get_llm_provider", lambda: _FakeLLM())
    return state


def test_profiles_all_documents_with_split_budget(wired):
    profile_task.profile_and_propose.run("job-1")

    final = wired["job_updates"][-1]
    assert final["status"] == "MODEL_PROPOSED"
    profile = json.loads(final["document_profile"])
    assert profile["total_pages"] == 24
    assert set(profile["sampled_pages_by_document"]) == {"doc-a", "doc-b"}
    # allocate_sampling_budget gives each doc its floor (SAMPLING_FLOOR=3) first,
    # then the remaining budget goes to doc-b (more unsampled pages):
    # doc-a=3, doc-b=12.
    assert profile["sampled_pages_by_document"]["doc-a"] == [1, 2, 3]
    assert len(profile["sampled_pages_by_document"]["doc-b"]) == 11

    call = wired["model_calls"][0]
    assert call["pages"] == 24
    assert "=== Document: a.pdf ===" in call["text"]
    assert "=== Document: b.pdf ===" in call["text"]
