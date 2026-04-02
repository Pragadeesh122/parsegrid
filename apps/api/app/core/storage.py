"""ParseGrid API — S3-compatible storage client.

Uses boto3 with configurable endpoint URL.
- Local dev: MinIO (http://localhost:9000)
- Production: AWS S3 (endpoint_url=None → uses default AWS endpoint)

Switching environments requires ONLY changing env vars — zero code changes.
"""

import boto3
from botocore.config import Config as BotoConfig

from app.core.config import settings

_s3_client = None


def get_s3_client():
    """Returns a cached boto3 S3 client. Works identically with MinIO and AWS S3."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=BotoConfig(signature_version="s3v4"),
        )
    return _s3_client


def generate_presigned_upload_url(
    object_key: str,
    content_type: str = "application/octet-stream",
    expires_in: int = 3600,
) -> str:
    """Generate a presigned URL for direct client-to-S3 upload.
    Avoids streaming large files through FastAPI.
    """
    client = get_s3_client()
    return client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_in,
    )


def generate_presigned_download_url(
    object_key: str,
    expires_in: int = 3600,
) -> str:
    """Generate a presigned URL for downloading a file from S3."""
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": object_key,
        },
        ExpiresIn=expires_in,
    )


def upload_file_to_s3(
    file_bytes: bytes,
    object_key: str,
    content_type: str = "application/octet-stream",
) -> None:
    """Upload file bytes directly to S3/MinIO."""
    client = get_s3_client()
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=object_key,
        Body=file_bytes,
        ContentType=content_type,
    )
