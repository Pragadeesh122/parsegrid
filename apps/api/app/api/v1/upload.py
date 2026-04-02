"""ParseGrid API — File upload endpoints.

Supports two modes:
1. Presigned URL: Client uploads directly to S3/MinIO (preferred for large files)
2. Direct upload: Client sends file to FastAPI, which forwards to S3 (small files)
"""

import uuid

from fastapi import APIRouter, Depends, File, UploadFile

from app.api.deps import get_current_user
from app.core.security import TokenPayload
from app.core.storage import generate_presigned_upload_url, upload_file_to_s3
from app.schemas.job import UploadUrlResponse

router = APIRouter(prefix="/upload", tags=["Upload"])


@router.post(
    "/presigned-url",
    response_model=UploadUrlResponse,
    summary="Get a presigned URL for direct-to-S3 upload",
)
async def get_upload_url(
    filename: str,
    content_type: str = "application/pdf",
    user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Generate a presigned PUT URL for the client to upload directly to
    MinIO/S3, bypassing FastAPI for large files.
    """
    file_key = f"uploads/{user.sub}/{uuid.uuid4()}/{filename}"
    upload_url = generate_presigned_upload_url(
        object_key=file_key,
        content_type=content_type,
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
    file_key = f"uploads/{user.sub}/{uuid.uuid4()}/{file.filename}"
    contents = await file.read()
    upload_file_to_s3(
        file_bytes=contents,
        object_key=file_key,
        content_type=file.content_type or "application/octet-stream",
    )
    return {"upload_url": "", "file_key": file_key}
