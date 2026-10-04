"""Cloud lifecycle aborts must not leave a durable job stuck on a dead upload ID."""
import hashlib
from types import SimpleNamespace

import pytest

from app.storage.cloud import OssStorage, S3Storage
from test_storage import FakeS3, MultipartOss


class ProviderFailure(Exception):
    def __init__(self, code):
        self.code = code
        self.response = {"Error": {"Code": code}}


@pytest.fixture(params=["s3", "oss"])
def upload(request, tmp_path):
    states = []

    class S3(FakeS3):
        created = 0
        missing_new = False
        old_error = "NoSuchUpload"

        def create_multipart_upload(self, **kwargs):
            self.created += 1
            self.parts.clear()
            return {"UploadId": "fresh"}

        def list_parts(self, UploadId, **kwargs):
            if UploadId == "expired" or self.missing_new:
                raise ProviderFailure(self.old_error)
            assert states[-1]["upload_id"] == UploadId
            return super().list_parts(**kwargs)

        def upload_part(self, UploadId, **kwargs):
            assert states[-1]["upload_id"] == UploadId == "fresh"
            return super().upload_part(**kwargs)

    class Oss(MultipartOss):
        created = 0
        missing_new = False
        old_error = "NoSuchUpload"

        def init_multipart_upload(self, key, **kwargs):
            self.created += 1
            self.parts.clear()
            return SimpleNamespace(upload_id="fresh")

        def list_parts(self, key, upload_id, **kwargs):
            if upload_id == "expired" or self.missing_new:
                raise ProviderFailure(self.old_error)
            assert states[-1]["upload_id"] == upload_id
            return super().list_parts(key, upload_id, **kwargs)

        def upload_part(self, key, upload_id, number, data):
            assert states[-1]["upload_id"] == upload_id == "fresh"
            return super().upload_part(key, upload_id, number, data)

    remote = S3() if request.param == "s3" else Oss()
    store = (S3Storage({"bucket": "fixture"}, {}, client=remote) if request.param == "s3"
             else OssStorage({}, {}, bucket=remote))
    store.part_size = 1024 * 1024
    source = tmp_path / "sample"
    source.write_bytes(b"test-content" * 100_000)
    checkpoint = {"key": "fixture/media", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "part_size": store.part_size, "upload_id": "expired", "completed_parts": 1}
    return remote, store, source, checkpoint, states


def test_expired_upload_is_replaced_and_checkpointed_before_upload(upload):
    remote, store, source, checkpoint, states = upload
    result = store.put_file(source, checkpoint["key"], "video/mp4", checkpoint=checkpoint,
                            on_checkpoint=states.append)
    assert remote.created == 1 and result.size == source.stat().st_size
    assert states[1] == {**{key: checkpoint[key] for key in ("key", "sha256", "part_size")},
                         "upload_id": "fresh"}
    assert checkpoint["upload_id"] == "expired"  # Do not mutate the caller's persisted snapshot.
    assert not remote.aborted
    store.verify(checkpoint["key"], checkpoint["sha256"], result.size)


@pytest.mark.parametrize("code", ["AccessDenied", "NoSuchBucket", "404"])
def test_other_provider_errors_never_discard_a_checkpoint(upload, code):
    remote, store, source, checkpoint, states = upload
    remote.old_error = code
    with pytest.raises(ProviderFailure) as failure:
        store.put_file(source, checkpoint["key"], "video/mp4", checkpoint=checkpoint,
                       on_checkpoint=states.append)
    assert failure.value.code == code
    assert remote.created == 0 and remote.uploaded == []
    assert states[-1] == checkpoint and not remote.aborted


def test_replacement_failure_does_not_loop_or_erase_new_checkpoint(upload):
    remote, store, source, checkpoint, states = upload
    remote.missing_new = True
    with pytest.raises(ProviderFailure):
        store.put_file(source, checkpoint["key"], "video/mp4", checkpoint=checkpoint,
                       on_checkpoint=states.append)
    assert remote.created == 1 and remote.uploaded == []
    assert states[-1]["upload_id"] == "fresh" and not remote.aborted


def test_cancelled_checkpoint_stops_before_any_replacement_parts(upload):
    from app.jobs import LeaseLost

    remote, store, source, checkpoint, states = upload

    def cancelled(state):
        states.append(state)
        if state["upload_id"] == "fresh":
            raise LeaseLost("Fixture cancellation")

    with pytest.raises(LeaseLost):
        store.put_file(source, checkpoint["key"], "video/mp4", checkpoint=checkpoint,
                       on_checkpoint=cancelled)
    assert remote.created == 1 and remote.uploaded == []
    assert remote.objects == {} and not remote.aborted
