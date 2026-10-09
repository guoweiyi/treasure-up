from __future__ import annotations

import os
import errno
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from .base import IntegrityError, ObjectInfo, ObjectMissing, Storage, StorageError, file_digest, safe_key, throttled_check


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

    def put_file(self, path: Path, key: str, mime_type: str, *, checkpoint=None, on_checkpoint=None, check_active=None) -> ObjectInfo:
        guard = throttled_check(check_active)
        expected, size = file_digest(path, check_active=guard)
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(key)
        temporary = destination.parent / f".upload-{uuid.uuid4().hex}"
        try:
            with Path(path).open("rb") as source, temporary.open("xb") as target:
                while block := source.read(1024 * 1024):
                    guard()
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if file_digest(temporary, check_active=guard) != (expected, size):
                raise IntegrityError("Source changed during copy")
            # A hard link publishes without overwriting a concurrent immutable object.
            self.path_for(key)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                pass
            self._sync_parents(destination)
            return self.verify(key, expected, size, check_active=guard)
        finally:
            temporary.unlink(missing_ok=True)

    def _sync_parents(self, destination):
        if os.name != "nt":
            for directory in [destination.parent, *destination.parent.parents]:
                if not directory.is_relative_to(self.root):
                    break
                fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)

    def consume_file(self, path, key, mime_type, *, sha256, size, check_active=None):
        """Publish an exclusively owned, finished temporary file; never an archive.

        The caller must own the temporary directory and relinquish every writer.
        Ordinary put_file intentionally keeps copying arbitrary caller inputs.
        """
        guard = throttled_check(check_active)
        path = Path(path)
        if _is_link(path):
            raise StorageError("Temporary source cannot be a link")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise StorageError("Temporary source must be an exclusive regular file")
            from .base import stream_digest
            if stream_digest(source, max_bytes=size, check_active=guard) != (sha256, size):
                raise IntegrityError("Temporary source SHA256 does not match")
            os.fsync(source.fileno())
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if _is_link(path) or identity(path.stat()) != identity(before):
                raise IntegrityError("Temporary source changed before publication")
            destination = self.path_for(key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.path_for(key)
            guard()
            try:
                os.link(path, destination)
            except FileExistsError:
                pass
            except OSError as exc:
                if exc.errno not in {errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP}:
                    raise
                # A separate filesystem or a provider without hard-link support
                # requires one final copy; never rename over an existing object.
                self.put_file(path, key, mime_type, check_active=guard)
            self._sync_parents(destination)
            result = self.verify(key, sha256, size, check_active=guard)
        # Unlink the private name only after full readback; retry can reuse the
        # immutable destination if registration or cancellation subsequently fails.
        guard()
        path.unlink()
        return result

    def delete(self, key: str, *, version_id=None):
        self.path_for(key).unlink(missing_ok=True)
