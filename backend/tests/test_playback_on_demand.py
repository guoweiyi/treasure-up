"""On-demand copies: preserve ordinary originals and convert only what needs it."""
import hashlib
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import delivery_api
from app.config import settings
from app.ingest import media, runner
from app.models import Asset, AssetRef, Base, Job, MediaVariant, User, Video, VideoPart


class PlaybackOnDemandTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.scratch = patch.object(settings, "scratch_dir", self.folder)
        self.scratch.start()
        self.video = Video(bvid="BV1234567890", title="Original", metadata_json={})
        self.db.add(self.video)
        self.db.flush()
        self.serial = 0

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.scratch.stop()
        self.tmp.cleanup()

    def asset(self, mime="video/mp4"):
        self.serial += 1
        asset = Asset(sha256=hashlib.sha256(str(self.serial).encode()).hexdigest(), size=1024,
                      mime_type=mime, kind="media")
        self.db.add(asset)
        self.db.flush()
        return asset

    def original(self, codec="h264", audio="aac", *, pixels="yuv420p", profile="LC", channels=2,
                 measured=True, mime="video/mp4"):
        asset = self.asset(mime)
        part = VideoPart(video_id=self.video.id, cid=str(self.serial), position=self.serial,
                         title="Part", duration=1)
        self.db.add(part)
        self.db.flush()
        archive = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key="original",
                               video_codec=codec, audio_codec=audio, width=64, height=64, duration=1)
        self.db.add(archive)
        self.db.flush()
        if measured:
            self.properties(archive, {"codec_name": codec, "pix_fmt": pixels, "hdr": False, "wide_gamut": False,
                "dolby_vision": False, "audio_present": bool(audio), "audio_codec": audio or None,
                "audio_profile": profile if audio else None, "audio_channels": channels if audio else None})
        return part, archive

    def properties(self, archive, properties):
        self.video.metadata_json = {**self.video.metadata_json, "media_properties": {
            **self.video.metadata_json.get("media_properties", {}), archive.id: properties}}
        self.db.flush()

    def context(self, originals, **policy):
        return SimpleNamespace(db=self.db, video=self.video, client=Mock(),
            policy={"create_compatible_copy": True, **policy},
            job=SimpleNamespace(kind="download_media", id="download", account_id=None),
            cp={"part_ids": [part.id for part, _ in originals],
                "media_parts": [part.id for part, _ in originals],
                "media_variants": {part.id: archive.id for part, archive in originals}},
            run=SimpleNamespace(id="run"), started=time.monotonic(), guard=Mock(), save=self.db.flush,
            stage=lambda *args, **kwargs: self.db.flush())

    def download(self, originals, **policy):
        with patch.object(runner, "_paid_media_allowed", return_value=True), \
             patch("app.maintenance.enqueue_variant_preparation") as prepare, \
             patch("app.jobs.enqueue", side_effect=lambda *a, **kw:
                   SimpleNamespace(id="copy-" + a[2], status="queued", result={})) as enqueue:
            self.assertTrue(runner._download(self.context(originals, **policy), self.video))
        return enqueue, prepare

    def conversion_source(self, codec="h264", audio="aac", **video_extra):
        return ({"codec_name": codec, "pix_fmt": "yuv420p", "width": 64, "height": 64, **video_extra},
                {"codec_name": audio, "profile": "LC", "channels": 2, "channel_layout": "stereo"}, 1)

    def test_ordinary_h264_hevc_av1_aac_never_enqueue_an_automatic_copy(self):
        originals = [self.original(codec) for codec in ("h264", "hevc", "av1")]
        enqueue, prepare = self.download(originals)
        enqueue.assert_not_called()
        self.assertEqual(prepare.call_count, 3)  # Only each original's normal preparation.
        self.assertEqual(self.video.metadata_json["ingest_state"]["playback"], "not_needed")
        for part, archive in originals:
            with patch.object(media, "materialize_asset") as materialize:
                self.assertEqual(media.ensure_playback_variant(self.db, part, archive, {}), (None, True))
            materialize.assert_not_called()
        self.assertEqual(list(self.db.scalars(select(MediaVariant).where(MediaVariant.kind == "playback"))), [])

    def test_explicitly_silent_original_needs_no_copy(self):
        part, archive = self.original(audio="")
        self.assertEqual(media.playback_requirement(self.db, part, archive), "not_needed")

    def test_incomplete_or_mismatched_measurements_are_not_assumed_compatible(self):
        part, archive = self.original()
        baseline = dict(self.video.metadata_json["media_properties"][archive.id])
        for key in ("pix_fmt", "audio_profile", "audio_channels", "audio_codec"):
            with self.subTest(missing=key):
                props = {k: v for k, v in baseline.items() if k != key}
                self.properties(archive, props)
                self.assertEqual(media.playback_requirement(self.db, part, archive), "unknown")
        self.properties(archive, {**baseline, "codec_name": "hevc"})
        self.assertEqual(media.playback_requirement(self.db, part, archive), "unknown")
        enqueue, _ = self.download([(part, archive)])
        enqueue.assert_called_once()

    def test_unknown_legacy_original_is_probed_then_skipped_without_a_variant(self):
        part, archive = self.original("av1", measured=False)
        legacy = MediaVariant(part_id=part.id, asset_id=archive.asset_id, kind="playback",
                              format_key="h264-aac-sdr-v2:" + archive.asset_id)
        self.db.add(legacy)
        self.db.flush()
        with patch.object(media, "materialize_asset", return_value=self.folder / "source") as materialize, \
             patch.object(media, "_probe", return_value=self.conversion_source("av1")) as probe, \
             patch.object(media, "_run_ffmpeg") as convert, patch.object(media, "ingest_file") as ingest:
            self.assertEqual(media.ensure_playback_variant(self.db, part, archive, {}), (None, True))
        materialize.assert_called_once()
        probe.assert_called_once()
        convert.assert_not_called()
        ingest.assert_not_called()
        self.assertEqual(list(self.db.scalars(select(MediaVariant.id).where(MediaVariant.kind == "playback"))), [legacy.id])
        self.assertEqual(media.playback_requirement(self.db, part, archive), "not_needed")

    def test_old_automatic_job_returns_not_needed_without_refs_or_prepare(self):
        part, archive = self.original("av1")
        job = Job(kind="create_playback", target_id=archive.id, status="running", policy={},
                  dedupe_key="playback:" + archive.id)
        self.db.add(job)
        self.db.commit()
        with patch("app.maintenance.enqueue_variant_preparation") as prepare:
            result = runner._create_playback(self.db, job)
        self.assertEqual(result["compatibility"], "not_needed")
        self.assertIsNone(result["variant_id"])
        self.assertEqual(job.checkpoint["progress"]["phase"], "compatible_not_needed")
        self.assertEqual(self.video.metadata_json["ingest_state"]["playback"], "not_needed")
        prepare.assert_not_called()
        self.assertEqual(list(self.db.scalars(select(AssetRef))), [])

    def test_mixed_parts_keep_the_required_audio_copy_and_correct_status(self):
        originals = [self.original("av1"), self.original("hevc", "eac3")]
        enqueue, prepare = self.download(originals)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[2], originals[1][1].id)
        self.assertEqual(prepare.call_count, 2)
        state = self.video.metadata_json["ingest_state"]
        self.assertEqual(state["playback"], "queued")
        ctx = self.context(originals)
        done = SimpleNamespace(id="audio-copy", status="succeeded", result={})
        runner._playback_state(ctx, self.video, originals[1][1], done)
        self.assertEqual(self.video.metadata_json["ingest_state"]["playback"], "complete")

    def test_disabling_copies_still_wins_for_special_audio(self):
        enqueue, _ = self.download([self.original(audio="flac")], create_compatible_copy=False)
        enqueue.assert_not_called()

    def test_non_aac_and_multichannel_audio_still_require_a_copy(self):
        for codec in ("eac3", "flac", "ac3", "truehd", "opus"):
            with self.subTest(codec=codec):
                part, archive = self.original("hevc", codec)
                self.assertEqual(media.playback_requirement(self.db, part, archive), "required")
        part, archive = self.original(channels=6)
        self.assertEqual(media.playback_requirement(self.db, part, archive), "required")

    def test_manual_old_device_request_still_converts_av1_aac_to_h264(self):
        part, archive = self.original("av1")
        converted = self.asset()
        def transcode(args, output, *unused):
            self.assertEqual(args[args.index("-c:v") + 1], "libx264")
            output.write_bytes(b"converted")
        with patch.object(media, "materialize_asset", return_value=self.folder / "source"), \
             patch.object(media, "_probe", side_effect=[self.conversion_source("av1"), self.conversion_source()]), \
             patch.object(media, "_run_ffmpeg", side_effect=transcode) as convert, \
             patch.object(media, "_verify_audio_decode"), patch.object(media, "ingest_file", return_value=converted):
            playback, reused = media.ensure_playback_variant(self.db, part, archive, {"compatibility_request": "manual"})
        convert.assert_called_once()
        self.assertEqual(playback.video_codec, "h264")
        self.assertNotEqual(playback.asset_id, archive.asset_id)
        self.assertFalse(reused)

    def test_manual_dolby_audio_copy_keeps_hevc_and_dolby_vision(self):
        part, archive = self.original("hevc", "eac3", pixels="yuv420p10le")
        source = self.conversion_source("hevc", "eac3", pix_fmt="yuv420p10le", color_transfer="smpte2084",
            color_primaries="bt2020", side_data_list=[{"side_data_type": "DOVI configuration record",
                "dv_profile": 8, "rpu_present_flag": 1}])
        output = self.asset()
        with patch.object(media, "materialize_asset", return_value=self.folder / "source"), \
             patch.object(media, "_probe", return_value=source), \
             patch.object(media, "_audio_compatible_copy", return_value=(source[0], self.conversion_source()[1], 1,
                 {"compatibility_mode": "audio_only", "video_stream_copy": True})) as audio_copy, \
             patch.object(media, "_run_ffmpeg") as full_transcode, patch.object(media, "ingest_file", return_value=output):
            playback, _ = media.ensure_playback_variant(self.db, part, archive, {"compatibility_request": "manual"})
        audio_copy.assert_called_once()
        full_transcode.assert_not_called()
        self.assertEqual(playback.video_codec, "hevc")
        self.assertEqual(playback.metadata_json["compatibility_mode"], "audio_only")

    def test_manual_api_does_not_reuse_an_automatic_av1_noop(self):
        part, archive = self.original("av1")
        automatic = Job(kind="create_playback", target_id=archive.id, status="queued", policy={},
                        dedupe_key="playback:" + archive.id)
        user = User(username="editor", password_hash="unused", role="admin")
        self.db.add_all([automatic, user])
        self.db.commit()
        result = delivery_api.compatible(archive.id, user=user, db=self.db)
        requested = self.db.get(Job, result["id"])
        self.assertNotEqual(requested.id, automatic.id)
        self.assertEqual(requested.policy["compatibility_request"], "manual")
        self.assertEqual(automatic.status, "queued")

    def test_manual_api_does_not_reuse_unknown_legacy_automatic_work(self):
        _, archive = self.original("av1", measured=False)
        automatic = Job(kind="create_playback", target_id=archive.id, status="queued", policy={},
                        dedupe_key="playback:" + archive.id)
        user = User(username="editor", password_hash="unused", role="admin")
        self.db.add_all([automatic, user])
        self.db.commit()
        result = delivery_api.compatible(archive.id, user=user, db=self.db)
        requested = self.db.get(Job, result["id"])
        self.assertNotEqual(requested.id, automatic.id)
        self.assertEqual(requested.policy["compatibility_request"], "manual")

    def test_skipped_part_does_not_replace_another_parts_pending_job_id(self):
        originals = [self.original("hevc", "eac3"), self.original("av1")]
        ctx = self.context(originals)
        pending = SimpleNamespace(id="needed-copy", status="queued", result={})
        skipped = SimpleNamespace(id="old-noop", status="succeeded", result={"compatibility": "not_needed"})
        runner._playback_state(ctx, self.video, originals[0][1], pending)
        runner._playback_state(ctx, self.video, originals[1][1], skipped)
        state = self.video.metadata_json["ingest_state"]
        self.assertEqual((state["playback"], state["playback_job_id"]), ("queued", pending.id))

    def test_manual_api_reuses_a_paused_audio_copy_without_resuming(self):
        _, archive = self.original("hevc", "eac3")
        automatic = Job(kind="create_playback", target_id=archive.id, status="paused", policy={},
                        dedupe_key="playback:" + archive.id)
        user = User(username="editor", password_hash="unused", role="admin")
        self.db.add_all([automatic, user])
        self.db.commit()
        result = delivery_api.compatible(archive.id, user=user, db=self.db)
        self.assertEqual(result["id"], automatic.id)
        self.assertEqual(automatic.status, "paused")

    @unittest.skipUnless(shutil.which(str(settings.ffmpeg_path)) and shutil.which(str(settings.ffprobe_path)),
                         "FFmpeg and ffprobe are required for the real stream-copy regression")
    def test_real_h264_flac_converts_only_audio_and_keeps_video_packet_hash(self):
        source = self.folder / "original.mp4"
        subprocess.run([str(settings.ffmpeg_path), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=64x64:rate=12", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "flac", "-strict", "-2",
            "-y", str(source)], check=True, capture_output=True, timeout=30)
        part, archive = self.original("h264", "flac")
        stored = self.asset()
        stored.size = source.stat().st_size
        published = []
        def ingest(db, path, **kwargs):
            video, audio, duration = media._probe(path)
            self.assertEqual((video["codec_name"], audio["codec_name"], audio["profile"], audio["channels"]),
                             ("h264", "aac", "LC", 2))
            self.assertEqual(media._packet_hash(source, "video", lambda: None),
                             media._packet_hash(path, "video", lambda: None))
            published.append(path.stat().st_size)
            return stored
        with patch.object(media, "materialize_asset", return_value=source), \
             patch.object(media, "ingest_file", side_effect=ingest):
            playback, _ = media.ensure_playback_variant(self.db, part, archive, {})
        self.assertEqual(len(published), 1)
        self.assertEqual(playback.metadata_json["compatibility_mode"], "audio_only")
        self.assertTrue(playback.metadata_json["video_payload_verified"])
        self.assertTrue(playback.metadata_json["audio_decode_verified"])
        self.assertEqual(playback.metadata_json["source_audio_codec"], "flac")


if __name__ == "__main__":
    unittest.main()
