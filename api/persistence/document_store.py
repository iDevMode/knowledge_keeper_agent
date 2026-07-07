"""Blob storage for generated handover documents.

Keeps the document bytes off ephemeral local disk so a download link doesn't
404 after a restart or on a different worker.

- local → filesystem directory (dev, or a single persistent container)
- s3    → S3/R2/any S3-compatible bucket (multi-worker / serverless)

boto3 is imported lazily so it's only required when DOCUMENT_STORAGE=s3.
Keys are `{document_id}.{ext}` (document_id is a UUID) — safe basenames.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)


class LocalDocumentBlobStore:
    def __init__(self, root: str):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Defend against traversal — keys are always simple basenames.
        return self._root / os.path.basename(key)

    def put(self, key: str, data: bytes) -> None:
        self._path(key).write_bytes(data)

    def read(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


class S3DocumentBlobStore:
    def __init__(self, bucket: str, endpoint_url: str = "", region: str = ""):
        import boto3  # noqa: PLC0415 — optional dependency

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region or None,
        )

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)

    def read(self, key: str) -> Optional[bytes]:
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=key)
            return obj["Body"].read()
        except self._client.exceptions.NoSuchKey:
            return None

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)


def create_blob_store():
    if settings.document_storage == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("DOCUMENT_STORAGE=s3 requires S3_BUCKET")
        logger.info("Using S3 document blob store (bucket=%s)", settings.s3_bucket)
        return S3DocumentBlobStore(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
        )
    return LocalDocumentBlobStore(settings.document_storage_path)
