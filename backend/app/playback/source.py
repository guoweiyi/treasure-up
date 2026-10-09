"""A task's verified, read-only input; local archives are never copied or chmodded."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import tempfile

from app.config import settings
from app.models import Asset
from app.storage.base import IntegrityError, stream_digest
from app.storage.local import LocalStorage, _is_link
from app.storage.locking import content_lock
from app.storage.service import asset_locations, get_adapter, materialize_asset
from .tools import PlaybackError


def _identity(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _make_writable(path):
    # Only our cloud materialization copy is chmodded for Windows cleanup.
    if path.exists() and not _is_link(path):
        path.chmod(0o600)


class VerifiedSource:
    def __init__(self, db, *, check_active=None):
        self.db = db
        self.guard = check_active or (lambda: None)
        self._stack = None
        self._identity = None
        self._path = None
        self._stat = None
        self._local = None

    def __enter__(self):
        if self._stack is not None or self._identity is not None:
            raise PlaybackError("Source scope cannot be reused")
        self._stack = ExitStack()
        return self

    def __exit__(self, *exc):
        stack, self._stack = self._stack, None
        try:
            if exc[0] is None and self._path is not None:
                self._validate()
        finally:
            stack.__exit__(*exc)

    def _validate(self):
        self.guard()
        if self._local is not None:
            adapter, key, stream = self._local
            if adapter.path_for(key) != self._path or _identity(os.fstat(stream.fileno())) != self._stat:
                raise PlaybackError("Verified source changed within the task")
        if _is_link(self._path) or not self._path.is_file() or _identity(self._path.stat()) != self._stat:
            raise PlaybackError("Verified source changed within the task")

    def _open_local(self, asset):
        rows = [(location, profile) for location, profile in asset_locations(self.db, asset.id) if profile.kind == "local"]
        if not rows:
            return False
        # Purge and migration use this same lock. It survives a loudness commit,
        # preventing deletion while FFmpeg uses the original read-only path.
        self._stack.enter_context(content_lock(self.db, asset.sha256))
        cancelled = False
        def check():
            nonlocal cancelled
            try:
                self.guard()
            except BaseException:
                cancelled = True
                raise
        for location, profile in rows:
            check()
            try:
                with ExitStack() as candidate:
                    adapter = get_adapter(profile)
                    path = adapter.path_for(location.object_key)
                    stream = candidate.enter_context(adapter.reader(location.object_key))
                    before = _identity(os.fstat(stream.fileno()))
                    if stream_digest(stream, max_bytes=asset.size, check_active=check) != (asset.sha256, asset.size):
                        raise IntegrityError("Source SHA256 does not match")
                    if (adapter.path_for(location.object_key) != path or _identity(path.stat()) != before
                            or _identity(os.fstat(stream.fileno())) != before):
                        raise IntegrityError("Source changed during verification")
                    self._stack.enter_context(candidate.pop_all())
                    self._path, self._stat = path, before
                    self._local = (adapter, location.object_key, stream)
                    return True
            except Exception:
                if cancelled:
                    raise
                # A damaged local replica may still have a valid cloud replica.
                continue
        return False

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
            if not self._open_local(asset):
                settings.scratch_dir.mkdir(parents=True, exist_ok=True)
                folder = self._stack.enter_context(tempfile.TemporaryDirectory(prefix="playback-source-", dir=settings.scratch_dir))
                path = materialize_asset(self.db, asset_id, Path(folder) / "source.media", check_active=self.guard)
                self.guard()
                self._stack.callback(_make_writable, path)
                path.chmod(0o400)
                self._path, self._stat = path, _identity(path.stat())
        self._validate()
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
