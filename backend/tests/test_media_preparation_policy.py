"""Automatic preparation stays opt-in; explicit work survives automatic opt-out."""
import hashlib
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app import delivery_api, maintenance
from app.models import Asset, Base, Job, MediaVariant, Setting, User, Video, VideoPart, utcnow
from app.schemas import MediaPreparationInput, PlaybackSettings


class MediaPreparationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.video = Video(bvid="BV1234567890", title="Original")
        self.user = User(username="admin", password_hash="unused", role="admin")
        self.db.add_all([self.video, self.user])
        self.db.flush()
        self.serial = 0

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def original(self, *, asset=None, part=None, kind="archive"):
        self.serial += 1
        if asset is None:
            asset = Asset(sha256=hashlib.sha256(str(self.serial).encode()).hexdigest(),
                size=8 * 1024**3, mime_type="video/mp4", kind="media")
            self.db.add(asset)
            self.db.flush()
        if part is None:
            part = VideoPart(video_id=self.video.id, cid=str(self.serial), position=self.serial,
                title="Part", duration=7200)
            self.db.add(part)
            self.db.flush()
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind=kind, format_key=str(self.serial),
            video_codec="h264", audio_codec="aac", duration=7200)
        self.db.add(variant)
        self.db.flush()
        return variant, asset, part

    def settings(self, **changes):
        value = PlaybackSettings(**changes).model_dump()
        row = self.db.get(Setting, "playback")
        if row:
            row.value = value
        else:
            self.db.add(Setting(key="playback", value=value))
        self.db.commit()

    def job(self, variant, *, manual=False, legacy=False, **actions):
        policy = {"package": True, "analyze_loudness": True, "segment_seconds": 6, **actions}
        if not legacy:
            policy["preparation_request"] = "manual" if manual else "automatic"
        job = Job(kind="prepare_media", target_id=variant.id, policy=policy, status="queued",
            dedupe_key=("prepare_media:" if manual else "prepare:v3:") + variant.id)
        self.db.add(job)
        self.db.commit()
        return job

    def run_preparation(self, job):
        with patch("app.playback.source.VerifiedSource") as provider, \
             patch("app.playback.analyze_variant", return_value={"status": "ready"}) as analyze, \
             patch("app.playback.package_variant", return_value=SimpleNamespace(id="hls")) as package:
            result = maintenance.run_media_preparation(self.db, job, check_active=Mock())
        return result, provider, analyze, package

    def test_defaults_do_not_schedule_large_originals_or_analyze_audio(self):
        config = PlaybackSettings()
        self.assertFalse(config.package_long_videos)
        self.assertFalse(config.analyze_loudness)
        variant, _, _ = self.original()
        with patch("app.jobs.enqueue") as enqueue:
            self.assertIsNone(maintenance.enqueue_variant_preparation(self.db, variant))
            self.assertTrue(maintenance._media_page(self.db)["disabled"])
        enqueue.assert_not_called()

    def test_old_frozen_automatic_queue_skips_before_opening_source(self):
        variant, _, _ = self.original()
        job = self.job(variant, legacy=True)
        result, provider, analyze, package = self.run_preparation(job)
        self.assertEqual(result["preparation"], "skipped")
        self.assertEqual(set(result["skipped_actions"]), {"package", "analyze_loudness"})
        provider.assert_not_called()
        analyze.assert_not_called()
        package.assert_not_called()

    def test_current_settings_can_disable_only_one_queued_action(self):
        variant, _, _ = self.original()
        job = self.job(variant, legacy=True)
        self.settings(analyze_loudness=True)
        result, _, analyze, package = self.run_preparation(job)
        self.assertEqual(result["completed_actions"], ["analyze_loudness"])
        self.assertEqual(result["skipped_actions"], ["package"])
        analyze.assert_called_once()
        package.assert_not_called()

    def test_legacy_explicit_manual_request_keeps_both_requested_actions(self):
        variant, _, _ = self.original()
        job = self.job(variant, manual=True, legacy=True)
        result, _, analyze, package = self.run_preparation(job)
        self.assertEqual(set(result["completed_actions"]), {"package", "analyze_loudness"})
        analyze.assert_called_once()
        package.assert_called_once()

    def test_manual_default_packages_without_implicit_loudness_scan(self):
        variant, _, _ = self.original()
        result = delivery_api.prepare(variant.id, user=self.user, db=self.db)
        job = self.db.get(Job, result["id"])
        self.assertEqual(job.policy["preparation_request"], "manual")
        self.assertTrue(job.policy["package"])
        self.assertFalse(job.policy["analyze_loudness"])
        _, _, analyze, package = self.run_preparation(job)
        analyze.assert_not_called()
        package.assert_called_once()

    def test_manual_audio_analysis_is_independent_and_empty_request_is_rejected(self):
        variant, _, _ = self.original()
        result = delivery_api.prepare(variant.id, MediaPreparationInput(package=False, analyze_loudness=True),
            user=self.user, db=self.db)
        _, _, analyze, package = self.run_preparation(self.db.get(Job, result["id"]))
        analyze.assert_called_once()
        package.assert_not_called()
        with self.assertRaises(HTTPException) as caught:
            delivery_api.prepare(variant.id, MediaPreparationInput(package=False), user=self.user, db=self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_manual_request_is_not_lost_when_automatic_settings_are_later_disabled(self):
        variant, _, _ = self.original()
        self.settings(package_long_videos=True)
        automatic = self.job(variant, analyze_loudness=False)
        result = delivery_api.prepare(variant.id, user=self.user, db=self.db)
        manual = self.db.get(Job, result["id"])
        self.assertNotEqual(manual.id, automatic.id)
        self.settings()
        manual_result, _, _, package = self.run_preparation(manual)
        self.assertEqual(manual_result["completed_actions"], ["package"])
        package.assert_called_once()
        auto_result, provider, _, _ = self.run_preparation(automatic)
        self.assertEqual(auto_result["preparation"], "skipped")
        provider.assert_not_called()

    def test_manual_request_reuses_paused_explicit_work_without_resuming(self):
        variant, _, _ = self.original()
        job = self.job(variant, manual=True)
        job.status = "paused"
        self.db.commit()
        result = delivery_api.prepare(variant.id, user=self.user, db=self.db)
        self.assertEqual(result["id"], job.id)
        self.assertEqual(job.status, "paused")

    def test_disabled_setting_stops_a_frozen_active_scan(self):
        self.db.add(Setting(key="media_maintenance_runtime", value={"active": True,
            "policy": PlaybackSettings(package_long_videos=True, analyze_loudness=True).model_dump(),
            "next_batch_at": (utcnow() + timedelta(days=1)).isoformat()}))
        self.db.commit()
        result = maintenance._media_page(self.db)
        self.assertTrue(result["disabled"])
        self.assertFalse(result["remaining_pending"])
        self.assertFalse(self.db.get(Setting, "media_maintenance_runtime").value["active"])

    def test_same_asset_alias_uses_one_original_task_without_crossing_parts(self):
        original, asset, part = self.original()
        alias, _, _ = self.original(asset=asset, part=part, kind="playback")
        other_part, _, _ = self.original(asset=asset)
        self.settings(package_long_videos=True)
        first = maintenance.enqueue_variant_preparation(self.db, original)
        self.assertEqual(maintenance.enqueue_variant_preparation(self.db, alias).id, first.id)
        self.assertNotEqual(maintenance.enqueue_variant_preparation(self.db, other_part).id, first.id)
        jobs = list(self.db.scalars(select(Job)))
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(not job.policy["analyze_loudness"] for job in jobs))

    def test_batch_aliases_reuse_preparation_and_completed_batch_reads_are_bounded(self):
        originals = []
        for _ in range(25):
            original, asset, part = self.original()
            self.original(asset=asset, part=part, kind="playback")
            originals.append(original)
        self.settings(package_long_videos=True)
        result = maintenance._media_page(self.db)
        self.assertEqual(result["queued_now"], len(originals))
        self.db.commit()
        for job in self.db.scalars(select(Job)):
            job.status, job.result = "succeeded", {"variant_id": "hls"}
        runtime = self.db.get(Setting, "media_maintenance_runtime")
        runtime.value = {**runtime.value, "next_run_at": (utcnow() - timedelta(seconds=1)).isoformat()}
        self.db.commit()
        statements = []
        def capture(*args):
            if args[2].lstrip().upper().startswith("SELECT"):
                statements.append(args[2])
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            self.assertEqual(maintenance._media_page(self.db)["queued_now"], 0)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertLessEqual(len(statements), 12)

    def test_reenabling_skipped_work_is_possible_but_failed_work_is_not_restarted(self):
        variant, _, _ = self.original()
        self.settings(package_long_videos=True)
        skipped = maintenance.enqueue_variant_preparation(self.db, variant)
        skipped.status = "succeeded"
        skipped.result = {"preparation": "skipped", "skipped_actions": ["package"]}
        self.db.commit()
        replacement = maintenance.enqueue_variant_preparation(self.db, variant)
        self.assertNotEqual(replacement.id, skipped.id)
        replacement.status = "failed"
        self.db.commit()
        self.assertEqual(maintenance.enqueue_variant_preparation(self.db, variant).id, replacement.id)


if __name__ == "__main__":
    unittest.main()
