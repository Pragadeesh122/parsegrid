"""Tier 3: append/resolve/delete-document guards and scoping."""

import pytest

from tests.integration.conftest import auth_header
from tests.integration.test_jobs_api import PAYLOAD, _create

pytestmark = pytest.mark.integration

APPEND_BODY = {"filename": "feb.pdf", "file_key": "uploads/u/z/feb.pdf", "file_size": 50}


@pytest.fixture
def no_celery(monkeypatch):
    from app.worker.tasks import ocr as ocr_tasks
    from app.worker.tasks import reconcile as reconcile_tasks

    monkeypatch.setattr(ocr_tasks.process_document, "apply_async", lambda *a, **k: None)
    monkeypatch.setattr(reconcile_tasks.rebuild_dataset, "apply_async", lambda *a, **k: None)


@pytest.fixture
async def db_exec():
    """Raw-SQL executor bound to the test database (same engine config as the app)."""
    import sqlalchemy
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.integration.conftest import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)

    async def _exec(sql: str, params: dict):
        async with engine.begin() as conn:
            await conn.execute(sqlalchemy.text(sql), params)

    yield _exec
    await engine.dispose()


async def _complete_job(client, db_exec, user="user-a"):
    """Create a job and force it COMPLETED directly in the DB."""
    job = await _create(client, user)
    await db_exec(
        "UPDATE jobs SET status = 'COMPLETED', progress = 100.0 WHERE id = :id",
        {"id": job["id"]},
    )
    await db_exec(
        "UPDATE documents SET status = 'EXTRACTED', "
        "extracted_buckets = '{\"tables\": {}}' WHERE job_id = :id",
        {"id": job["id"]},
    )
    return job


async def test_append_requires_completed(client, no_celery):
    job = await _create(client, "user-a")  # status UPLOADED
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents",
        json=APPEND_BODY,
        headers=auth_header("user-a"),
    )
    assert res.status_code == 409


async def test_append_rejects_targeted(client, no_celery, db_exec):
    res = await client.post(
        "/api/v1/jobs",
        json={**PAYLOAD, "job_type": "TARGETED"},
        headers=auth_header("user-a"),
    )
    job = res.json()
    await db_exec("UPDATE jobs SET status = 'COMPLETED' WHERE id = :id", {"id": job["id"]})
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents",
        json=APPEND_BODY,
        headers=auth_header("user-a"),
    )
    assert res.status_code == 400


async def test_append_happy_path_sets_appending(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents",
        json=APPEND_BODY,
        headers=auth_header("user-a"),
    )
    assert res.status_code == 201
    assert res.json()["status"] == "PENDING"
    refreshed = await client.get(f"/api/v1/jobs/{job['id']}", headers=auth_header("user-a"))
    assert refreshed.json()["status"] == "APPENDING"
    assert refreshed.json()["document_count"] == 2


async def test_append_is_user_scoped(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents",
        json=APPEND_BODY,
        headers=auth_header("user-b"),
    )
    assert res.status_code == 404


async def test_cannot_delete_last_contributing_document(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    doc_id = job["documents"][0]["id"]
    res = await client.delete(
        f"/api/v1/jobs/{job['id']}/documents/{doc_id}",
        headers=auth_header("user-a"),
    )
    assert res.status_code == 400


async def test_rebuild_requires_locked_model(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    res = await client.post(f"/api/v1/jobs/{job['id']}/rebuild", headers=auth_header("user-a"))
    # locked_model is null on this seeded job → 400
    assert res.status_code == 400


async def test_rebuild_requires_extracted_documents(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    await db_exec(
        'UPDATE jobs SET locked_model = \'{"extraction_type": "single_table", '
        '"tables": [], "relationships": []}\' WHERE id = :id',
        {"id": job["id"]},
    )
    await db_exec("UPDATE documents SET status = 'FAILED' WHERE job_id = :id", {"id": job["id"]})
    res = await client.post(f"/api/v1/jobs/{job['id']}/rebuild", headers=auth_header("user-a"))
    assert res.status_code == 400


async def test_resolve_stamps_resolution_into_compat_report(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    doc_id = job["documents"][0]["id"]
    await db_exec(
        "UPDATE jobs SET status = 'AWAITING_APPEND_REVIEW' WHERE id = :id", {"id": job["id"]}
    )
    await db_exec(
        'UPDATE documents SET compat_report = \'{"needs_review": true, "reasons": []}\' '
        "WHERE id = :id",
        {"id": doc_id},
    )
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents/{doc_id}/resolve",
        json={"action": "cancel"},
        headers=auth_header("user-a"),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "REJECTED"
    assert body["compat_report"]["needs_review"] is False
    assert body["compat_report"]["resolved"] == "cancel"

    # A second resolve on the same (now stale) document must be rejected.
    await db_exec(
        "UPDATE jobs SET status = 'AWAITING_APPEND_REVIEW' WHERE id = :id", {"id": job["id"]}
    )
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents/{doc_id}/resolve",
        json={"action": "cancel"},
        headers=auth_header("user-a"),
    )
    assert res.status_code == 400
