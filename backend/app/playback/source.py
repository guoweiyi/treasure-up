"""A single task's lazy, verified source; never a cross-job media cache."""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import tempfile

from app.config import settings
from app.models import Asset
from app.storage.service import materialize_asset
from .tools import PlaybackError


def _make_writable(path):
    # Windows cannot unlink a read-only file. Restore only this private copy's
    # mode before TemporaryDirectory cleanup, never follow a replaced symlink.
    if path.exists() and not path.is_symlink():
        path.chmod(0o600)


class VerifiedSource:
    def __init__(self, db, *, check_active=None):
        self.db = db
        self.guard = check_active or (lambda: None)
        self._stack = None
        self._identity = None
        self._path = None
        self._stat = None

    def __enter__(self):
        if self._stack is not None or self._identity is not None:
            raise PlaybackError("Source scope cannot be reused")
        self._stack = ExitStack()
        return self

    def __exit__(self, *exc):
        stack, self._stack = self._stack, None
        return stack.__exit__(*exc)

    def __call__(self, asset_id):
        if self._stack is None:
            raise PlaybackError("Source scope is not active")
        self.guard()
        asset = self.db.get(Asset, asset_id)
        if asset is None:
            raise PlaybackError("Source media asset does not exist")
        identity = (asset.id, asset.sha256, asset.size)
        if self._identity is not None and self._identity != identity:
            raise PlaybackError("Source media identity changed within the task")
        if self._path is None:
            self._identity = identity
            settings.scratch_dir.mkdir(parents=True, exist_ok=True)
            folder = self._stack.enter_context(tempfile.TemporaryDirectory(prefix="playback-source-", dir=settings.scratch_dir))
            path = materialize_asset(self.db, asset_id, Path(folder) / "source.media")
            self.guard()
            self._stack.callback(_make_writable, path)
            path.chmod(0o400)
            self._path = path
            stat = path.stat()
            self._stat = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        stat = self._path.stat()
        if self._path.is_symlink() or not self._path.is_file() or (
                stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != self._stat:
            raise PlaybackError("Verified source changed within the task")
        return self._path


@contextmanager
def source_scope(db, *, source_provider=None, check_active=None):
    if source_provider is not None:
        if not isinstance(source_provider, VerifiedSource) or source_provider.db is not db:
            raise PlaybackError("Invalid source scope")
        yield source_provider
    else:
        with VerifiedSource(db, check_active=check_active) as provider:
            yield provider
