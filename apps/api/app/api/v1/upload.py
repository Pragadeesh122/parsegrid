"""ParseGrid API — File upload endpoints.

Supports two modes:
1. Presigned URL: Client uploads directly to S3/MinIO (preferred for large files)
2. Direct upload: Client sends file to FastAPI, which forwards to S3 (small files)

Both modes enforce the configured size cap and content-type allowlist.
"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import TokenPayload
from app.core.storage import generate_presigned_upload_url, upload_file_to_s3
from app.schemas.job import UploadUrlResponse

router = APIRouter(prefix="/upload", tags=["Upload"])


def _validate_upload(content_type: str, size: int) -> None:
    if content_type not in settings.allowed_upload_content_types:
        raise HTTPException(status_code=415, detail=f"Unsupported content type: {content_type}")
    if size <= 0 or size > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File exceeds the {limit_mb} MB upload limit")


@router.post(
    "/presigned-url",
    response_model=UploadUrlResponse,
    summary="Get a presigned URL for direct-to-S3 upload",
)
async def get_upload_url(
    filename: str,
    file_size: int,
    content_type: str = "application/pdf",
    user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Generate a presigned PUT URL for the client to upload directly to
    MinIO/S3. The declared size is part of the signature, so the client
    cannot upload more bytes than it declared.
    """
    _validate_upload(content_type, file_size)
    file_key = f"uploads/{user.sub}/{uuid.uuid4()}/{filename}"
    upload_url = generate_presigned_upload_url(
        object_key=file_key,
        content_type=content_type,
        content_length=file_size,
    )
    return {"upload_url": upload_url, "file_key": file_key}


@router.post(
    "/direct",
    response_model=UploadUrlResponse,
    summary="Upload a file directly through FastAPI",
)
async def direct_upload(
    file: UploadFile = File(...),
    user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Upload a file through FastAPI (for small files < 10MB).
    For larger files, use the presigned URL endpoint instead.
    """
    # Validate against the parsed part size BEFORE buffering the body in RAM.
    _validate_upload(file.content_type or "application/octet-stream", file.size or 0)
    contents = await file.read()
    file_key = f"uploads/{user.sub}/{uuid.uuid4()}/{file.filename}"
    upload_file_to_s3(
        file_bytes=contents,
        object_key=file_key,
        content_type=file.content_type or "application/octet-stream",
    )
    return {"upload_url": "", "file_key": file_key}
