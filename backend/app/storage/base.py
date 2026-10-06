from __future__ import annotations

import hashlib
import re
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class StorageError(RuntimeError):
    pass


class ObjectMissing(StorageError):
    pass


class IntegrityError(StorageError):
    pass


def safe_key(key: str) -> str:
    # Keep URL delimiters, controls, bidi markers and Windows path syntax out of
    # keys, while allowing readable CJK names. Providers escape these as URLs.
    if (not isinstance(key, str) or not key or len(key.encode("utf-8", errors="surrogatepass")) > 1024
            or not re.match(r"[A-Za-z0-9]", key)
            or any(not (character in "._/- []()" or unicodedata.category(character)[0] in "LNM"
                        or unicodedata.category(character) == "So") for character in key)):
        raise StorageError("Invalid storage key")
    parts = key.split("/")
    if any(p in ("", ".", "..") or p.endswith((".", " ")) or p.startswith(" ") or len(p.encode("utf-8")) > 255 for p in parts):
        raise StorageError("Invalid storage key")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(unicodedata.normalize("NFKC", p.split(".")[0]).rstrip(" .").upper() in reserved for p in parts):
        raise StorageError("Invalid storage key")
    return key


def file_digest(path: Path) -> tuple[str, int]:
    with Path(path).open("rb") as stream:
        return stream_digest(stream)


def stream_digest(stream: BinaryIO, *, max_bytes: int | None = None) -> tuple[str, int]:
    digest, count = hashlib.sha256(), 0
    while block := stream.read(min(1024 * 1024, max_bytes - count + 1) if max_bytes is not None else 1024 * 1024):
        count += len(block)
        if max_bytes is not None and count > max_bytes:
            raise IntegrityError("Object exceeds registered size")
        digest.update(block)
    return digest.hexdigest(), count


def content_key(sha256: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise StorageError("Invalid content digest")
    return f"assets/sha256/{sha256[:2]}/{sha256}"


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    etag: str | None = None
    version_id: str | None = None
    checksum: str | None = None


class Storage:
    kind: str

    def head(self, key: str, *, version_id: str | None = None) -> ObjectInfo:
        raise NotImplementedError

    @contextmanager
    def reader(self, key: str, *, version_id: str | None = None):
        raise NotImplementedError
        yield

    def put_file(self, path: Path, key: str, mime_type: str, *, checkpoint=None, on_checkpoint=None) -> ObjectInfo:
        raise NotImplementedError

    def verify(self, key: str, sha256: str, size: int, *, version_id: str | None = None) -> ObjectInfo:
        info = self.head(key, version_id=version_id)
        if info.size != size:
            raise IntegrityError("Stored object size does not match")
        with self.reader(key, version_id=version_id) as stream:
            actual = stream_digest(stream, max_bytes=size)
        if actual != (sha256, size):
            raise IntegrityError("Stored object SHA256 does not match")
        return info

    def download_to(self, key: str, destination: Path, *, version_id: str | None = None, max_bytes: int | None = None):
        with self.reader(key, version_id=version_id) as source, Path(destination).open("wb") as target:
            count = 0
            while block := source.read(min(1024 * 1024, max_bytes - count + 1) if max_bytes is not None else 1024 * 1024):
                count += len(block)
                if max_bytes is not None and count > max_bytes:
                    raise IntegrityError("Object exceeds registered size")
                target.write(block)

    def read_range(self, key: str, start: int, end: int, *, version_id: str | None = None) -> bytes:
        if start < 0 or end < start:
            raise StorageError("Invalid byte range")
        with self.reader(key, version_id=version_id) as stream:
            stream.seek(start)
            return stream.read(end - start + 1)
