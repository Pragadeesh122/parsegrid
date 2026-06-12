# apps/api/tests/test_ocr_task.py
"""Tier 2: per-document OCR task wiring (faked S3/OCR/DB)."""

from dataclasses import dataclass, field

import pytest

from app.worker.tasks import ocr as ocr_task


@dataclass
class _FakeOCRResult:
    page_count: int = 2
    full_text: str = "hello"
    pages: list = field(default_factory=list)


class _Recorder:
    def __init__(self):
        self.job_updates = []
        self.doc_updates = []
        self.published = []
        self.uploaded_keys = []


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    rec.job_status = "OCR_PROCESSING"
    monkeypatch.setattr(ocr_task, "update_job", lambda job_id, **kw: rec.job_updates.append(kw))
    monkeypatch.setattr(
        ocr_task,
        "get_job_field",
        lambda job_id, *cols: {"status": rec.job_status},
    )
    monkeypatch.setattr(
        ocr_task,
        "update_document",
        lambda doc_id, **kw: rec.doc_updates.append((doc_id, kw)),
    )
    monkeypatch.setattr(
        ocr_task,
        "publish_status",
        lambda job_id, status, progress, **kw: rec.published.append((status, kw)),
    )
    monkeypatch.setattr(
        ocr_task,
        "get_document_field",
        lambda doc_id, *cols: {"file_key": "uploads/u/x/a.pdf", "filename": "a.pdf"},
    )

    class _FakeS3:
        def download_file(self, bucket, key, path):
            pass

    monkeypatch.setattr("app.core.storage.get_s3_client", lambda: _FakeS3())
    monkeypatch.setattr(
        "app.core.storage.upload_file_to_s3",
        lambda file_bytes, object_key, content_type: rec.uploaded_keys.append(object_key),
    )

    class _FakeProvider:
        def process_document(self, path):
            return _FakeOCRResult()

    monkeypatch.setattr("app.providers.factory.get_ocr_provider", lambda: _FakeProvider())
    return rec


def test_create_mode_writes_per_document_paths_and_returns_doc_id(recorder, monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        "app.worker.tasks.extract.run_extraction",
        type("T", (), {"apply_async": staticmethod(lambda **kw: dispatched.append(kw))}),
    )
    result = ocr_task.process_document.run("job-1", "doc-1")
    assert result == "doc-1"
    assert recorder.uploaded_keys == [
        "parsed/job-1/doc-1/full_text.txt",
        "parsed/job-1/doc-1/ocr_result.json",
    ]
    assert ("doc-1", {"status": "OCR_DONE", "page_count": 2}) in recorder.doc_updates
    assert dispatched == []  # chord callback advances creation, not this task


def test_append_mode_dispatches_single_document_extraction(recorder, monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        "app.worker.tasks.extract.run_extraction",
        type("T", (), {"apply_async": staticmethod(lambda **kw: dispatched.append(kw))}),
    )
    ocr_task.process_document.run("job-1", "doc-2", append=True)
    assert dispatched == [{"args": ["job-1"], "kwargs": {"document_id": "doc-2"}}]


def test_create_mode_never_resurrects_a_failed_job(recorder, monkeypatch):
    # A sibling document's terminal failure already marked the job FAILED;
    # this document's start must not flip it back to OCR_PROCESSING.
    recorder.job_status = "FAILED"
    monkeypatch.setattr(
        "app.worker.tasks.extract.run_extraction",
        type("T", (), {"apply_async": staticmethod(lambda **kw: None)}),
    )
    ocr_task.process_document.run("job-1", "doc-4")
    assert {"status": "OCR_PROCESSING", "progress": 5.0} not in recorder.job_updates


def test_append_failure_keeps_job_completed(recorder, monkeypatch):
    class _BoomProvider:
        def process_document(self, path):
            raise RuntimeError("ocr exploded")

    monkeypatch.setattr("app.providers.factory.get_ocr_provider", lambda: _BoomProvider())
    # Swallowed, not re-raised: re-raising would fire the task_failure signal,
    # which overwrites the COMPLETED status back to FAILED.
    result = ocr_task.process_document.run("job-1", "doc-3", append=True)
    assert result is None
    assert any(d == "doc-3" and kw.get("status") == "FAILED" for d, kw in recorder.doc_updates)
    assert {"status": "COMPLETED", "progress": 100.0} in recorder.job_updates
