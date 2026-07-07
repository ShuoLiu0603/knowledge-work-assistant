from __future__ import annotations

from io import BytesIO

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings


def get_minio_client() -> Minio:
    settings = get_settings()
    return Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def ensure_bucket() -> None:
    settings = get_settings()
    client = get_minio_client()
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)


def upload_bytes(object_key: str, data: bytes, content_type: str | None = None) -> None:
    settings = get_settings()
    ensure_bucket()
    get_minio_client().put_object(
        bucket_name=settings.minio_bucket,
        object_name=object_key,
        data=BytesIO(data),
        length=len(data),
        content_type=content_type or "application/octet-stream",
    )


def download_bytes(object_key: str) -> bytes:
    settings = get_settings()
    response = get_minio_client().get_object(settings.minio_bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def remove_object(object_key: str) -> None:
    settings = get_settings()
    try:
        get_minio_client().remove_object(settings.minio_bucket, object_key)
    except S3Error as exc:
        if exc.code in {"NoSuchBucket", "NoSuchKey", "NoSuchObject"}:
            return
        raise
