import io

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.core.security import TokenPayload
from app.main import app


@pytest.fixture
async def client(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: TokenPayload(
        {"sub": "user-1", "email": "u@x.com", "name": "U"}
    )
    # Never touch real storage in these tests.
    monkeypatch.setattr(
        "app.api.v1.upload.generate_presigned_upload_url",
        lambda **kwargs: "https://example.com/presigned",
    )
    monkeypatch.setattr("app.api.v1.upload.upload_file_to_s3", lambda **kwargs: None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_presigned_url_happy_path(client):
    res = await client.post(
        "/api/v1/upload/presigned-url",
        params={"filename": "doc.pdf", "file_size": 1024, "content_type": "application/pdf"},
    )
    assert res.status_code == 200
    assert res.json()["upload_url"] == "https://example.com/presigned"


async def test_presigned_url_rejects_oversize(client):
    res = await client.post(
        "/api/v1/upload/presigned-url",
        params={
            "filename": "doc.pdf",
            "file_size": 200 * 1024 * 1024,
            "content_type": "application/pdf",
        },
    )
    assert res.status_code == 413


async def test_presigned_url_rejects_disallowed_content_type(client):
    res = await client.post(
        "/api/v1/upload/presigned-url",
        params={"filename": "x.exe", "file_size": 10, "content_type": "application/x-msdownload"},
    )
    assert res.status_code == 415


async def test_presigned_url_requires_file_size(client):
    res = await client.post("/api/v1/upload/presigned-url", params={"filename": "doc.pdf"})
    assert res.status_code == 422


async def test_direct_upload_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.max_upload_bytes", 100)
    big = io.BytesIO(b"x" * 200)
    res = await client.post(
        "/api/v1/upload/direct",
        files={"file": ("doc.pdf", big, "application/pdf")},
    )
    assert res.status_code == 413


async def test_direct_upload_rejects_disallowed_content_type(client):
    res = await client.post(
        "/api/v1/upload/direct",
        files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
    )
    assert res.status_code == 415


async def test_direct_upload_happy_path(client):
    res = await client.post(
        "/api/v1/upload/direct",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert res.status_code == 200
    assert res.json()["file_key"].startswith("uploads/user-1/")
