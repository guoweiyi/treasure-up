from __future__ import annotations

import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from .base import IntegrityError, ObjectInfo, ObjectMissing, Storage, StorageError, file_digest, safe_key


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


class LocalStorage(Storage):
    kind = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root).absolute()
        if _is_link(self.root):
            raise StorageError("Storage root cannot be a symbolic link")
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()

    def path_for(self, key: str) -> Path:
        if _is_link(self.root):
            raise StorageError("Storage root cannot be a symbolic link")
        path = self.root
        for component in safe_key(key).split("/"):
            path = path / component
            if _is_link(path):
                raise StorageError("Symbolic links are forbidden in storage")
        if not path.resolve().is_relative_to(self.root):
            raise StorageError("Storage path escapes root")
        return path

    @contextmanager
    def reader(self, key: str, *, version_id=None):
        path = self.path_for(key)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        except FileNotFoundError as exc:
            raise ObjectMissing("Object does not exist") from exc
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise StorageError("Object must be a regular file")
            yield stream

    def head(self, key: str, *, version_id=None) -> ObjectInfo:
        with self.reader(key) as stream:
            return ObjectInfo(os.fstat(stream.fileno()).st_size)

    def put_file(self, path: Path, key: str, mime_type: str, *, checkpoint=None, on_checkpoint=None) -> ObjectInfo:
        expected, size = file_digest(path)
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(key)
        temporary = destination.parent / f".upload-{uuid.uuid4().hex}"
        try:
            with Path(path).open("rb") as source, temporary.open("xb") as target:
                while block := source.read(1024 * 1024):
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if file_digest(temporary) != (expected, size):
                raise IntegrityError("Source changed during copy")
            # A hard link publishes without overwriting a concurrent immutable object.
            self.path_for(key)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                pass
            if os.name != "nt":
                for directory in [destination.parent, *destination.parent.parents]:
                    if not directory.is_relative_to(self.root):
                        break
                    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            return self.verify(key, expected, size)
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self, key: str, *, version_id=None):
        self.path_for(key).unlink(missing_ok=True)
