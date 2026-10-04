import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.models import Asset, AssetLocation, Base, StorageProfile
from app.storage.base import IntegrityError, StorageError, content_key, safe_key
from app.storage.cloud import OssStorage, S3Storage
from app.storage.local import LocalStorage
from app.storage.service import ingest_file, materialize_asset, migrate_asset, probe_profile, read_asset_bytes, resolve_asset


@pytest.fixture
def storage_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


@pytest.mark.parametrize("key", ["../secret", "/etc/passwd", "a/../b", "a\\b", "a//b", "a/./b", "a%2fb", "C:/secret", "a/NUL.txt", "a/x."])
def test_rejects_unsafe_keys(key):
    with pytest.raises(StorageError):
        safe_key(key)


def test_local_immutable_and_symlink_boundary(tmp_path):
    store = LocalStorage(tmp_path / "objects")
    source = tmp_path / "source"
    source.write_bytes(b"original")
    store.put_file(source, "safe/object", "text/plain")
    source.write_bytes(b"changed")
    with pytest.raises(IntegrityError):
        store.put_file(source, "safe/object", "text/plain")
    assert store.path_for("safe/object").read_bytes() == b"original"
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (store.root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic-link creation unavailable on this host")
    with pytest.raises(StorageError):
        store.path_for("escape/secret")


def test_ingest_deduplicates_and_migration_survives_source_loss(storage_db, tmp_path):
    db = storage_db
    source = tmp_path / "source"
    source.write_bytes(b"media bytes" * 100)
    asset = ingest_file(db, source, kind="video", mime_type="video/mp4")
    again = ingest_file(db, source, kind="video", mime_type="video/mp4")
    assert asset.id == again.id
    assert len(list(db.scalars(select(Asset)))) == 1
    first = resolve_asset(db, asset.id)
    target = StorageProfile(name="other", kind="local", config={"root": str(tmp_path / "replica")}, enabled=True, is_default=False)
    db.add(target)
    db.flush()
    migrate_asset(db, asset.id, target.id)
    migrate_asset(db, asset.id, target.id)
    assert len(list(db.scalars(select(AssetLocation)))) == 2
    first["path"].unlink()
    assert read_asset_bytes(db, asset.id) == source.read_bytes()
    assert resolve_asset(db, asset.id)["path"].is_relative_to(tmp_path / "replica")


def test_corrupt_existing_object_never_marked_ready(storage_db, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"good")
    root = LocalStorage(settings.media_root)
    key = content_key(hashlib.sha256(b"good").hexdigest())
    path = root.path_for(key)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"evil")
    with pytest.raises(IntegrityError):
        ingest_file(storage_db, source, kind="video", mime_type="video/mp4")
    assert not list(storage_db.scalars(select(Asset)))


def test_materialize_restores_hash_and_rejects_escape_or_overwrite(storage_db, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"archived protobuf sample")
    asset = ingest_file(storage_db, source, kind="danmaku", mime_type="application/octet-stream")
    destination = settings.scratch_dir / "run" / "segment.pb"
    assert materialize_asset(storage_db, asset.id, destination).read_bytes() == source.read_bytes()
    assert materialize_asset(storage_db, asset.id, destination) == destination
    with pytest.raises(StorageError):
        materialize_asset(storage_db, asset.id, tmp_path / "outside")
    destination.write_bytes(b"keep existing")
    with pytest.raises(IntegrityError):
        materialize_asset(storage_db, asset.id, destination)
    assert destination.read_bytes() == b"keep existing"


def test_probe_reports_scope_and_cleans_object(storage_db):
    profile = StorageProfile(name="probe", kind="local", config={"root": str(settings.media_root)}, enabled=True, is_default=True)
    storage_db.add(profile)
    storage_db.flush()
    result = probe_profile(storage_db, profile.id)
    assert result["status"] == "passed"
    assert result["multipart"] == "not_tested"
    assert not list((settings.media_root / "probes").iterdir())


class MissingS3(Exception):
    response = {"Error": {"Code": "404"}}


class FakeS3:
    def __init__(self):
        self.objects, self.parts, self.uploaded = {}, {}, []
        self.fail_part = None
        self.aborted = False

    def head_object(self, Bucket, Key, **kwargs):
        if Key not in self.objects:
            raise MissingS3()
        return {"ContentLength": len(self.objects[Key]), "ETag": "not-an-md5", "VersionId": "v1"}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body.read()

    def get_object(self, Bucket, Key, Range=None, **kwargs):
        data = self.objects[Key]
        if Range:
            start, end = map(int, Range.removeprefix("bytes=").split("-"))
            return {"Body": io.BytesIO(data[start:end + 1]), "ContentRange": f"bytes {start}-{end}/{len(data)}", "ResponseMetadata": {"HTTPStatusCode": 206}}
        return {"Body": io.BytesIO(data)}

    def create_multipart_upload(self, **kwargs):
        return {"UploadId": "upload1"}

    def list_parts(self, **kwargs):
        return {"Parts": [{"PartNumber": number, "Size": len(data), "ETag": str(number)} for number, data in self.parts.items()]}

    def upload_part(self, PartNumber, Body, **kwargs):
        if PartNumber == self.fail_part:
            raise OSError("simulated interruption")
        self.parts[PartNumber] = Body
        self.uploaded.append(PartNumber)
        return {"ETag": str(PartNumber)}

    def complete_multipart_upload(self, Key, MultipartUpload, **kwargs):
        self.objects[Key] = b"".join(self.parts[item["PartNumber"]] for item in MultipartUpload["Parts"])

    def abort_multipart_upload(self, **kwargs):
        self.aborted = True

    def delete_object(self, Key, **kwargs):
        self.objects.pop(Key, None)

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        assert operation == "get_object"
        return f"https://storage.invalid/{Params['Key']}?expires={ExpiresIn}"


def test_s3_multipart_resume_checks_source_and_content(tmp_path):
    client = FakeS3()
    store = S3Storage({"bucket": "private", "prefix": "tenant", "part_size": 5 * 1024**2}, {}, client=client)
    source = tmp_path / "video"
    source.write_bytes(b"a" * (6 * 1024**2))
    states = []
    client.fail_part = 2
    with pytest.raises(OSError):
        store.put_file(source, "video/test", "video/mp4", on_checkpoint=states.append)
    assert not client.aborted and client.uploaded == [1]
    client.fail_part = None
    store.put_file(source, "video/test", "video/mp4", checkpoint=states[-1], on_checkpoint=states.append)
    assert client.uploaded == [1, 2]
    store.verify("video/test", hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size)
    assert store.read_range("video/test", 9, 20) == b"a" * 12
    assert "expires=30" in store.presign("video/test", 30)
    source.write_bytes(b"other")
    # Use a multipart-size replacement to exercise checkpoint binding.
    source.write_bytes(b"b" * (6 * 1024**2))
    with pytest.raises(StorageError):
        store.put_file(source, "video/test", "video/mp4", checkpoint=states[-1])


class FakeOss:
    def __init__(self):
        self.objects = {}

    def put_object(self, key, stream, **kwargs):
        self.objects[key] = stream.read()

    def head_object(self, key, **kwargs):
        return SimpleNamespace(content_length=len(self.objects[key]), etag="opaque", versionid="oss-v1", server_crc=123)

    def get_object(self, key, byte_range=None, **kwargs):
        data = self.objects[key]
        if byte_range:
            start, end = byte_range
            result = io.BytesIO(data[start:end + 1])
            result.status, result.headers = 206, {"Content-Range": f"bytes {start}-{end}/{len(data)}"}
            return result
        return io.BytesIO(data)

    def sign_url(self, method, key, lifetime, **kwargs):
        assert method == "GET"
        return f"https://oss.invalid/{key}?ttl={lifetime}"

    def delete_object(self, key, **kwargs):
        self.objects.pop(key, None)


class MultipartOss(FakeOss):
    def __init__(self):
        super().__init__()
        self.parts, self.uploaded, self.fail_part = {}, [], None
        self.aborted = False

    def init_multipart_upload(self, key, **kwargs):
        return SimpleNamespace(upload_id="oss-upload")

    def list_parts(self, key, upload_id, **kwargs):
        return SimpleNamespace(parts=[SimpleNamespace(part_number=n, size=len(data), etag=str(n)) for n, data in self.parts.items()], is_truncated=False)

    def upload_part(self, key, upload_id, number, data):
        if number == self.fail_part:
            raise OSError("simulated OSS interruption")
        self.parts[number] = data
        self.uploaded.append(number)
        return SimpleNamespace(etag=str(number))

    def complete_multipart_upload(self, key, upload_id, parts):
        self.objects[key] = b"".join(self.parts[part.part_number] for part in parts)

    def abort_multipart_upload(self, key, upload_id):
        self.aborted = True


def test_native_oss_multipart_resume_and_uncheckpointed_abort(tmp_path):
    bucket = MultipartOss()
    store = OssStorage({"part_size": 1024**2}, {}, bucket=bucket)
    source = tmp_path / "oss-video"
    source.write_bytes(b"v" * (1024**2 + 100))
    states = []
    bucket.fail_part = 2
    with pytest.raises(OSError):
        store.put_file(source, "asset/video", "video/mp4", on_checkpoint=states.append)
    assert bucket.uploaded == [1] and not bucket.aborted
    bucket.fail_part = None
    store.put_file(source, "asset/video", "video/mp4", checkpoint=states[-1], on_checkpoint=states.append)
    assert bucket.uploaded == [1, 2]
    store.verify("asset/video", hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size)
    bucket.fail_part = 2
    bucket.parts = {}
    with pytest.raises(OSError):
        store.put_file(source, "asset/another", "video/mp4")
    assert bucket.aborted


def test_native_oss_readback_range_and_signing(tmp_path):
    bucket = FakeOss()
    store = OssStorage({"prefix": "archive"}, {}, bucket=bucket)
    source = tmp_path / "data"
    source.write_bytes(b"0123456789")
    info = store.put_file(source, "asset/a", "video/mp4")
    assert info.etag == "opaque" and info.version_id == "oss-v1"
    store.verify("asset/a", hashlib.sha256(source.read_bytes()).hexdigest(), 10)
    assert store.read_range("asset/a", 2, 5) == b"2345"
    assert "ttl=30" in store.presign("asset/a", 30)


def test_oversized_provider_stream_is_stopped_before_disk_amplification(tmp_path):
    from contextlib import contextmanager
    from app.storage.base import ObjectInfo, Storage

    class Endless(io.RawIOBase):
        read_bytes = 0
        def read(self, size=-1):
            assert size > 0, "Unbounded provider read"
            self.read_bytes += size
            return b'x' * size

    class Provider(Storage):
        streams = []
        def head(self, *args, **kwargs):
            return ObjectInfo(64)
        @contextmanager
        def reader(self, *args, **kwargs):
            with Endless() as stream:
                self.streams.append(stream)
                yield stream

    provider = Provider()
    target = tmp_path / 'bounded'
    with pytest.raises(IntegrityError, match='exceeds'):
        provider.download_to('asset', target, max_bytes=64)
    assert target.stat().st_size <= 64
    with pytest.raises(IntegrityError, match='exceeds'):
        provider.verify('asset', hashlib.sha256(b'x' * 64).hexdigest(), 64)
    assert all(stream.read_bytes == 65 and stream.closed for stream in provider.streams)


def test_placement_snapshot_is_deep_and_ignores_tuning():
    from app.storage.service import storage_placement
    config = {'root': '/test', 'read_priority': 1, 'part_size': 5242880, 'nested': {'value': 'old'}}
    snapshot = storage_placement('local', config)
    config['nested']['value'] = 'new'
    assert snapshot == {'kind': 'local', 'config': {'root': '/test', 'nested': {'value': 'old'}}}


def test_existing_fallback_profile_does_not_take_configuration_lock(storage_db, tmp_path, monkeypatch):
    from app.storage import service
    profile = StorageProfile(name='fallback', kind='local', config={'root': str(tmp_path)}, is_default=False)
    storage_db.add(profile); storage_db.commit()
    monkeypatch.setattr(service, 'lock_storage_configuration', lambda _: pytest.fail('Read-only fallback must not lock configuration'))
    assert service.default_profile(storage_db).id == profile.id


@pytest.mark.parametrize('change', ['root', 'disabled', 'tuning'])
def test_ingest_publication_rechecks_fresh_profile(storage_db, tmp_path, monkeypatch, change):
    from sqlalchemy import update
    db = storage_db
    profile = StorageProfile(name='target', kind='local', config={'root': str(tmp_path / 'old')}, is_default=True)
    db.add(profile); db.commit()
    source = tmp_path / 'sample'
    source.write_bytes(b'new object')
    put = LocalStorage.put_file
    def update_during_upload(store, *args, **kwargs):
        result = put(store, *args, **kwargs)
        values = {'config': {'root': str(tmp_path / 'new')}} if change == 'root' else (
            {'enabled': False} if change == 'disabled' else {'config': {**profile.config, 'read_priority': 99}})
        db.execute(update(StorageProfile).where(StorageProfile.id == profile.id).values(**values)
                   .execution_options(synchronize_session=False))
        return result
    monkeypatch.setattr(LocalStorage, 'put_file', update_during_upload)
    if change == 'tuning':
        ingest_file(db, source, kind='media', mime_type='video/mp4')
        assert len(list(db.scalars(select(AssetLocation)))) == 1
    else:
        with pytest.raises(StorageError, match='placement changed'):
            ingest_file(db, source, kind='media', mime_type='video/mp4')
        assert not list(db.scalars(select(AssetLocation)))
