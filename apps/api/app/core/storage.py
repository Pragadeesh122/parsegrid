"""ParseGrid API — S3-compatible storage client.

Uses boto3 with configurable endpoint URL.
- Local dev: MinIO (http://localhost:9000)
- Production: AWS S3 (endpoint_url=None → uses default AWS endpoint)

Switching environments requires ONLY changing env vars — zero code changes.
"""

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

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


def delete_object_from_s3(object_key: str) -> None:
    """Delete a single object from S3/MinIO if it exists."""
    delete_objects_from_s3([object_key])


def delete_prefix_from_s3(prefix: str) -> int:
    """Delete all objects under a prefix. Returns the number deleted."""
    client = get_s3_client()
    paginator = client.get_paginator("list_objects_v2")
    object_keys: list[str] = []

    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
        contents = page.get("Contents", [])
        if not contents:
            continue
        object_keys.extend(obj["Key"] for obj in contents)

    return delete_objects_from_s3(object_keys)


def delete_objects_from_s3(object_keys: list[str]) -> int:
    """Delete many objects and raise if MinIO/S3 reports failures."""
    if not object_keys:
        return 0

    client = get_s3_client()
    deleted = 0

    for start in range(0, len(object_keys), 1000):
        chunk = object_keys[start : start + 1000]
        try:
            response = client.delete_objects(
                Bucket=settings.s3_bucket,
                Delete={
                    "Objects": [{"Key": key} for key in chunk],
                    "Quiet": False,
                },
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"NoSuchKey", "404"}:
                continue
            raise

        errors = response.get("Errors", [])
        if errors:
            details = ", ".join(
                f"{error.get('Key', '?')}: {error.get('Code', 'Unknown')}"
                for error in errors
            )
            raise RuntimeError(f"Failed to delete S3 objects: {details}")

        deleted += len(response.get("Deleted", []))

    return deleted
