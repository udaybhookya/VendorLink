from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from vl_core.settings import Settings, get_settings


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str:
        """Store bytes under `key`; return a URI for `get`."""
        ...

    async def get(self, uri: str) -> bytes: ...


def _safe_key(key: str) -> PurePosixPath:
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"invalid blob key: {key!r}")
    return path


class LocalBlobStore:
    """Files under a local directory; URIs look like `local://<key>`."""

    scheme = "local://"

    def __init__(self, root: Path) -> None:
        self.root = root

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        path = self.root / _safe_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"{self.scheme}{key}"

    async def get(self, uri: str) -> bytes:
        if not uri.startswith(self.scheme):
            raise ValueError(f"not a local blob uri: {uri!r}")
        return (self.root / _safe_key(uri.removeprefix(self.scheme))).read_bytes()


class GcsBlobStore:
    """Objects in one GCS bucket; URIs look like `gs://<bucket>/<key>`. Reads are limited to
    that bucket, so a stored URI can never point the service at someone else's data."""

    def __init__(self, settings: Settings, client: Any = None) -> None:
        if not settings.gcs_bucket:
            raise ValueError("VL_BLOB=gcs needs GCS_BUCKET")
        if client is None:
            from google.cloud import storage

            client = storage.Client(project=settings.google_cloud_project or None)
        self._bucket = client.bucket(settings.gcs_bucket)
        self.prefix = f"gs://{settings.gcs_bucket}/"

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        name = str(_safe_key(key))
        blob = self._bucket.blob(name)
        await asyncio.to_thread(blob.upload_from_string, data, content_type=content_type)
        return f"{self.prefix}{name}"

    async def get(self, uri: str) -> bytes:
        if not uri.startswith(self.prefix):
            raise ValueError(f"not a blob in this bucket: {uri!r}")
        blob = self._bucket.blob(str(_safe_key(uri.removeprefix(self.prefix))))
        data: bytes = await asyncio.to_thread(blob.download_as_bytes)
        return data


def get_blob_store(settings: Settings | None = None) -> BlobStore:
    settings = settings or get_settings()
    if settings.vl_blob == "gcs":
        return GcsBlobStore(settings)
    return LocalBlobStore(settings.vl_data_dir / "blobs")
