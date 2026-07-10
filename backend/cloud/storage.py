"""Object-storage abstraction.

The rest of the app only knows this interface, so swapping the local-filesystem
dev backend for S3/GCS/R2 in prod is a one-class change. The client contract is
identical either way: mint a key, PUT bytes to a presigned URL, later GET them
from a presigned URL.
"""
from __future__ import annotations

import abc
import os
import shutil
import uuid
from datetime import datetime
from typing import Tuple

from . import config


def new_media_key(filename: str, prefix: str = "uploads") -> str:
    """Build a collision-free storage key that keeps the original extension."""
    ext = os.path.splitext(filename or "")[1].lower()
    stamp = datetime.utcnow().strftime("%Y/%m")
    return f"{prefix}/{stamp}/{uuid.uuid4().hex}{ext}"


class Storage(abc.ABC):
    @abc.abstractmethod
    def presign_put(self, key: str) -> Tuple[str, int]:
        """Return (url, expires_in_seconds) the client PUTs raw bytes to."""

    @abc.abstractmethod
    def presign_get(self, key: str) -> Tuple[str, int]:
        """Return (url, expires_in_seconds) the client GETs bytes from."""

    @abc.abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abc.abstractmethod
    def size(self, key: str) -> int:
        ...

    @abc.abstractmethod
    def put_file(self, key: str, src_path: str) -> None:
        """Upload a local file to `key` (used by workers to store results)."""

    @abc.abstractmethod
    def fetch_to(self, key: str, dest_path: str) -> str:
        """Download `key` to `dest_path` and return it (workers need a real
        local file for ffmpeg / Whisper)."""


class LocalStorage(Storage):
    """Filesystem-backed dev storage. Objects live under DATA_DIR/objects/<key>;
    presigned URLs are plain /storage/<key> URLs served by this same app."""

    def __init__(self, root: str, public_base_url: str, ttl: int):
        self.root = os.path.join(root, "objects")
        self.public_base_url = public_base_url.rstrip("/")
        self.ttl = ttl
        os.makedirs(self.root, exist_ok=True)

    def _abs(self, key: str) -> str:
        # Guard against path traversal escaping the object root.
        path = os.path.normpath(os.path.join(self.root, key))
        if not path.startswith(os.path.abspath(self.root) + os.sep) and path != os.path.abspath(self.root):
            raise ValueError(f"Illegal storage key: {key!r}")
        return path

    def _url(self, key: str) -> str:
        return f"{self.public_base_url}/storage/{key}"

    def presign_put(self, key: str) -> Tuple[str, int]:
        return self._url(key), self.ttl

    def presign_get(self, key: str) -> Tuple[str, int]:
        return self._url(key), self.ttl

    def exists(self, key: str) -> bool:
        return os.path.isfile(self._abs(key))

    def size(self, key: str) -> int:
        return os.path.getsize(self._abs(key))

    def put_file(self, key: str, src_path: str) -> None:
        dst = self._abs(key)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src_path, dst)

    def fetch_to(self, key: str, dest_path: str) -> str:
        src = self._abs(key)
        if not os.path.isfile(src):
            raise FileNotFoundError(key)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copyfile(src, dest_path)
        return dest_path

    # Dev-only helpers used by the /storage PUT+GET routes.
    def write_bytes(self, key: str, data: bytes) -> None:
        dst = self._abs(key)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(data)

    def abs_path(self, key: str) -> str:
        return self._abs(key)


def build_storage() -> Storage:
    if config.STORAGE_BACKEND == "local":
        return LocalStorage(config.DATA_DIR, config.PUBLIC_BASE_URL, config.PRESIGN_TTL)
    # Prod: an S3Storage(Storage) using boto3 presigned URLs slots in here.
    raise NotImplementedError(
        f"storage backend {config.STORAGE_BACKEND!r} not implemented "
        "(only 'local' is wired today)")
