import pytest

from tests.integration.conftest import auth_header

pytestmark = pytest.mark.integration

PAYLOAD = {"filename": "inv.pdf", "file_key": "uploads/u/x/inv.pdf", "file_size": 123}


async def _create(client, user: str) -> dict:
    res = await client.post("/api/v1/jobs", json=PAYLOAD, headers=auth_header(user))
    assert res.status_code in (200, 201)
    return res.json()


async def test_create_returns_uploaded_job(client):
    job = await _create(client, "user-a")
    assert job["status"] == "UPLOADED"
    assert len(job["id"]) == 36  # string UUID


async def test_get_is_scoped_to_owner(client):
    job = await _create(client, "user-a")
    own = await client.get(f"/api/v1/jobs/{job['id']}", headers=auth_header("user-a"))
    assert own.status_code == 200
    foreign = await client.get(f"/api/v1/jobs/{job['id']}", headers=auth_header("user-b"))
    assert foreign.status_code == 404


async def test_list_is_scoped_to_owner(client):
    await _create(client, "user-a")
    res = await client.get("/api/v1/jobs", headers=auth_header("user-b"))
    assert res.status_code == 200
    body = res.json()
    assert body["jobs"] == []


async def test_delete_is_scoped_to_owner(client):
    job = await _create(client, "user-a")
    res = await client.delete(f"/api/v1/jobs/{job['id']}", headers=auth_header("user-b"))
    assert res.status_code == 404


async def test_unauthenticated_request_rejected(client):
    res = await client.get("/api/v1/jobs")
    assert res.status_code in (401, 403)  # HTTPBearer yields 403 when header absent


async def test_connection_test_blocks_internal_target(client):
    res = await client.post(
        "/api/v1/connections/test",
        json={
            "connection_string": "postgresql://x:y@localhost:5436/parsegrid",
            "output_format": "SQL",
        },
        headers=auth_header("user-a"),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False
    assert "internal" in body["message"].lower()
