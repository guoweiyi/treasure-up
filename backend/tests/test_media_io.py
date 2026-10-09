"""Media processing reads local archives and publishes owned outputs without copies."""
import errno
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Asset, AssetLocation, Base, MediaVariant, StorageProfile, Video, VideoPart
from app.playback.source import VerifiedSource
from app.playback.tools import PlaybackError
from app.storage.base import IntegrityError, Storage, StorageError, content_key, file_digest
from app.storage.local import LocalStorage
from app.storage.service import ingest_file, ingest_workspace, materialize_asset


class Cancelled(Exception):
    pass


class MemoryStorage(Storage):
    def __init__(self, payload):
        self.payload, self.reads = payload, 0

    @contextmanager
    def reader(self, key, *, version_id=None):
        self.reads += 1
        yield io.BytesIO(self.payload)


class MediaIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.media, self.scratch = self.folder / "media", self.folder / "scratch"
        self.patches = [patch.object(settings, "media_root", self.media),
                        patch.object(settings, "scratch_dir", self.scratch)]
        for item in self.patches:
            item.start()
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.profile = StorageProfile(name="Local", kind="local", config={"root": str(self.media)},
                                      enabled=True, is_default=True)
        self.db.add(self.profile)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def archive(self, payload=b"original video bytes"):
        source = self.folder / "input.mp4"
        source.write_bytes(payload)
        asset = ingest_file(self.db, source, kind="media", mime_type="video/mp4")
        self.db.commit()
        location = self.db.scalar(select(AssetLocation).where(AssetLocation.asset_id == asset.id))
        return asset, LocalStorage(self.media).path_for(location.object_key)

    def remote(self, payload):
        self.profile.kind = "s3"
        self.profile.config = {}
        asset = Asset(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload), kind="media", mime_type="video/mp4")
        self.db.add(asset)
        self.db.flush()
        self.db.add(AssetLocation(asset_id=asset.id, storage_profile_id=self.profile.id,
                                  object_key=content_key(asset.sha256), state="ready"))
        self.db.commit()
        return asset

    def test_local_source_is_read_directly_once_without_chmod_or_scratch(self):
        asset, original = self.archive()
        before = original.stat()
        from app.playback import source as sources
        with patch.object(sources, "materialize_asset", side_effect=AssertionError("unnecessary copy")), \
             patch.object(sources, "stream_digest", wraps=sources.stream_digest) as digest:
            with VerifiedSource(self.db) as source:
                self.assertEqual(source(asset.id), original)
                self.db.commit()  # The same protected input survives separate analysis commit.
                self.assertEqual(source(asset.id), original)
            digest.assert_called_once()
        after = original.stat()
        self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns),
                         (after.st_ino, after.st_mode, after.st_mtime_ns))
        self.assertEqual(file_digest(original), (asset.sha256, asset.size))
        self.assertFalse(self.scratch.exists())

    def test_source_detects_rewrite_even_when_size_and_mtime_are_restored(self):
        asset, original = self.archive()
        with self.assertRaises(PlaybackError):
            with VerifiedSource(self.db) as source:
                source(asset.id)
                stamp = original.stat()
                original.write_bytes(b"x" * asset.size)
                os.utime(original, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertTrue(original.exists())

    def test_corrupt_local_replica_never_becomes_a_verified_source(self):
        asset, original = self.archive()
        original.write_bytes(b"x" * asset.size)
        with self.assertRaises(IntegrityError):
            with VerifiedSource(self.db) as source:
                source(asset.id)
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(original.read_bytes(), b"x" * asset.size)

    def test_cancellation_during_verification_does_not_fallback_or_change_original(self):
        asset, original = self.archive(b"x" * (2 * 1024**2))
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls >= 4:
                raise Cancelled("stop")
        with patch("app.playback.source.materialize_asset", side_effect=AssertionError("cancel must escape")):
            with self.assertRaises(Cancelled):
                with VerifiedSource(self.db, check_active=check) as source:
                    source(asset.id)
        # A fresh scope proves every handle/lock was released on cancellation.
        with VerifiedSource(self.db) as source:
            self.assertEqual(source(asset.id), original)
        self.assertEqual(file_digest(original), (asset.sha256, asset.size))
        self.assertFalse(self.scratch.exists())

    def test_remote_materialization_downloads_once_and_does_not_copy_scratch(self):
        payload = b"remote media" * 10000
        asset = self.remote(payload)
        remote = MemoryStorage(payload)
        with patch("app.storage.service.get_adapter", return_value=remote), \
             patch.object(LocalStorage, "put_file", side_effect=AssertionError("second full copy")):
            with VerifiedSource(self.db) as source:
                private = source(asset.id)
                self.assertEqual(private.read_bytes(), payload)
                self.assertEqual(source(asset.id), private)
            self.assertFalse(private.exists())
        self.assertEqual(remote.reads, 1)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_remote_cancellation_removes_partial_scratch_and_is_not_retried(self):
        asset = self.remote(b"remote" * 500000)
        remote = MemoryStorage(b"remote" * 500000)
        def check():
            if remote.reads:
                raise Cancelled("stop copying")
        with patch("app.storage.service.get_adapter", return_value=remote):
            with self.assertRaises(Cancelled):
                with VerifiedSource(self.db, check_active=check) as source:
                    source(asset.id)
        self.assertEqual(remote.reads, 1)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_materialization_never_overwrites_conflicting_destination(self):
        asset, original = self.archive()
        self.scratch.mkdir()
        target = self.scratch / "existing"
        target.write_bytes(b"keep")
        with self.assertRaises(IntegrityError):
            materialize_asset(self.db, asset.id, target)
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertTrue(original.exists())

    def test_owned_output_publishes_same_inode_without_put_file(self):
        with ingest_workspace(self.db, prefix="hls-") as work:
            self.assertTrue(work.path.is_relative_to(self.media))
            output = work.path / "segment.m4s"
            output.write_bytes(b"fragment payload")
            inode = output.stat().st_ino
            with patch.object(LocalStorage, "put_file", side_effect=AssertionError("unnecessary output copy")):
                asset = ingest_file(self.db, output, kind="hls_segment", mime_type="video/iso.segment", workspace=work)
            self.assertFalse(output.exists())
            published = LocalStorage(self.media).path_for(content_key(asset.sha256))
            self.assertEqual(published.stat().st_ino, inode)
            self.assertEqual(file_digest(published), (asset.sha256, asset.size))
        self.assertFalse(work.path.exists())
        self.assertTrue(published.exists())

    def test_workspace_rejects_archive_aliases_and_foreign_paths(self):
        asset, original = self.archive()
        with ingest_workspace(self.db) as work:
            alias = work.path / "alias.mp4"
            os.link(original, alias)
            for path in (alias, original):
                with self.assertRaises(StorageError):
                    ingest_file(self.db, path, kind="media", mime_type="video/mp4", workspace=work)
            link = work.path / "link.mp4"
            try:
                link.symlink_to(original)
            except OSError:
                pass  # Windows without symbolic-link permission still checks hard links.
            else:
                with self.assertRaises(StorageError):
                    ingest_file(self.db, link, kind="media", mime_type="video/mp4", workspace=work)
        self.assertEqual(file_digest(original), (asset.sha256, asset.size))

    def test_cancelled_output_is_removed_without_publishing_and_workspace_cannot_be_reused(self):
        with self.assertRaises(Cancelled):
            with ingest_workspace(self.db) as work:
                output = work.path / "segment.m4s"
                output.write_bytes(b"partial")
                ingest_file(self.db, output, kind="hls_segment", mime_type="video/iso.segment", workspace=work,
                            check_active=Mock(side_effect=Cancelled("stop")))
        self.assertFalse(work.path.exists())
        self.assertEqual(list(self.db.scalars(select(Asset))), [])
        with self.assertRaises(StorageError):
            work.__enter__()

    def test_storage_reconfiguration_during_generation_is_rejected(self):
        with ingest_workspace(self.db) as work:
            output = work.path / "output.mp4"
            output.write_bytes(b"complete")
            self.profile.config = {"root": str(self.folder / "other")}
            with self.assertRaises(StorageError):
                ingest_file(self.db, output, kind="media", mime_type="video/mp4", workspace=work)
            self.assertTrue(output.exists())

    def test_cross_filesystem_consume_falls_back_to_one_copy(self):
        store = LocalStorage(self.media)
        source = self.folder / "owned"
        source.write_bytes(b"verified output")
        sha, size = file_digest(source)
        original_link = os.link
        calls = 0
        def cross_mount_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.EXDEV, "cross device")
            return original_link(*args, **kwargs)
        with patch("app.storage.local.os.link", side_effect=cross_mount_once), \
             patch.object(store, "put_file", wraps=store.put_file) as copy:
            store.consume_file(source, content_key(sha), "video/mp4", sha256=sha, size=size)
        copy.assert_called_once()
        self.assertFalse(source.exists())
        self.assertEqual(file_digest(store.path_for(content_key(sha))), (sha, size))

    def test_hash_checks_cancellation_by_time_instead_of_querying_per_megabyte(self):
        from app.storage.base import stream_digest
        guard = Mock()
        payload = b"x" * (4 * 1024**2)
        with patch("app.storage.base.time.monotonic", return_value=100.0):
            self.assertEqual(stream_digest(io.BytesIO(payload), check_active=guard),
                             (hashlib.sha256(payload).hexdigest(), len(payload)))
        self.assertEqual(guard.call_count, 2)  # Start and completion, not every MiB.

    @unittest.skipUnless(shutil.which(str(settings.ffmpeg_path)) and shutil.which(str(settings.ffprobe_path)), "FFmpeg required")
    def test_real_compatible_audio_uses_original_directly_and_consumes_only_its_output(self):
        from app.ingest.media import ensure_playback_variant, _packet_hash, _probe
        source = self.folder / "tiny-flac.mp4"
        subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=64x64:rate=12", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "1", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "flac",
            "-strict", "-2", "-y", str(source)], check=True, capture_output=True, timeout=30)
        asset, original = self.archive(source.read_bytes())
        video = Video(bvid="BV1234567890", title="IO fixture", metadata_json={})
        self.db.add(video)
        self.db.flush()
        part = VideoPart(video_id=video.id, cid="1", position=1, title="Part", duration=1)
        self.db.add(part)
        self.db.flush()
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="original",
            video_codec="h264", audio_codec="flac", width=64, height=64, duration=1)
        self.db.add(variant)
        self.db.commit()
        before = original.stat()
        with patch("app.playback.source.materialize_asset", side_effect=AssertionError("input copied")), \
             patch.object(LocalStorage, "put_file", side_effect=AssertionError("output copied")):
            result, reused = ensure_playback_variant(self.db, part, variant, {})
        self.assertFalse(reused)
        self.assertTrue(result.metadata_json["video_payload_verified"])
        self.assertTrue(result.metadata_json["audio_decode_verified"])
        location = self.db.scalar(select(AssetLocation).where(AssetLocation.asset_id == result.asset_id))
        output = LocalStorage(self.media).path_for(location.object_key)
        self.assertEqual(_probe(output)[1]["codec_name"], "aac")
        self.assertEqual(_packet_hash(original, "video", lambda: None), _packet_hash(output, "video", lambda: None))
        self.assertEqual(file_digest(original), (asset.sha256, asset.size))
        self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns),
                         (original.stat().st_ino, original.stat().st_mode, original.stat().st_mtime_ns))
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(list((self.media / "temporary").iterdir()), [])

    @unittest.skipUnless(shutil.which(str(settings.ffmpeg_path)) and shutil.which(str(settings.ffprobe_path)), "FFmpeg required")
    def test_real_hls_keeps_payloads_and_uses_no_full_input_or_output_copies(self):
        from app.playback.hls import package_variant, load_hls_index
        source = self.folder / "tiny.mp4"
        subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=64x64:rate=12", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "4", "-c:v", "libx264", "-threads", "1", "-g", "12", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-y", str(source)], check=True, capture_output=True, timeout=30)
        asset, original = self.archive(source.read_bytes())
        video = Video(bvid="BV1234567890", title="IO fixture", metadata_json={})
        self.db.add(video)
        self.db.flush()
        part = VideoPart(video_id=video.id, cid="1", position=1, title="Part", duration=4)
        self.db.add(part)
        self.db.flush()
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="original",
            video_codec="h264", audio_codec="aac", width=64, height=64, duration=4)
        self.db.add(variant)
        self.db.commit()
        original_stat = original.stat()
        with patch("app.playback.source.materialize_asset", side_effect=AssertionError("input copied")), \
             patch.object(LocalStorage, "put_file", side_effect=AssertionError("output copied")):
            result = package_variant(self.db, variant.id, segment_seconds=2)
        self.db.commit()
        self.assertTrue(result.metadata_json["payloads_verified"])
        index = load_hls_index(self.db, result.asset_id)
        self.assertGreaterEqual(len(index["segments"]), 2)
        for row in index["segments"]:
            segment = self.db.get(Asset, row["asset_id"])
            self.assertEqual(file_digest(LocalStorage(self.media).path_for(content_key(segment.sha256))),
                             (segment.sha256, segment.size))
        self.assertEqual(file_digest(original), (asset.sha256, asset.size))
        self.assertEqual((original.stat().st_ino, original.stat().st_mode, original.stat().st_mtime_ns),
                         (original_stat.st_ino, original_stat.st_mode, original_stat.st_mtime_ns))
        self.assertFalse(self.scratch.exists())
        self.assertEqual(list((self.media / "temporary").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
