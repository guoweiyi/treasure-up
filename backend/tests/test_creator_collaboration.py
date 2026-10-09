"""Joint submissions must work without accepting unrelated creator captures."""

import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import creator_capture_api as capture_api
from app import source_monitoring
from app.ingest import runner
from app.ingest.errors import IngestError
from app.models import Base, Collection, Creator, PlatformUser, Setting, User, Video, VideoCreator


UID = "184904514"
OWNER_UID = "403748305"
BVID = "BV1TjTd6sEGZ"


def details():
    # Shape observed on the real joint submission that broke the entire page.
    return {"bvid": BVID, "aid": 4242, "title": "Joint submission", "duration": 128,
        "owner": {"mid": int(OWNER_UID), "name": "Submitting creator"},
        "staff": [{"mid": int(OWNER_UID), "name": "Submitting creator", "title": "UP主"},
                  {"mid": int(UID), "name": "Participant", "title": "参演"}],
        "pages": [{"cid": 9001, "page": 1, "part": "Joint submission", "duration": 128}]}


def listing_row(**changes):
    return {"bvid": BVID, "aid": 4242, "mid": int(OWNER_UID), "title": "Joint submission",
        "is_union_video": 1, "created": 1720000000, "length": "2:08", **changes}


class CreatorCollaborationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.person = PlatformUser(uid=UID, display_name="Participant")
        self.user = User(username="editor", password_hash="unused", role="editor")
        self.db.add_all([self.person, self.user])
        self.db.flush()
        self.creator = Creator(user_id=self.person.id)
        self.db.add(self.creator)
        self.db.commit()
        self.account = SimpleNamespace(id="account", secret_encrypted="test-generation")
        self.client = Mock()
        self.client.view.return_value = details()
        self.patches = [
            patch.object(capture_api, "_account", return_value=self.account),
            patch.object(capture_api, "_client", side_effect=lambda *_: nullcontext((self.client, {}))),
            patch.object(capture_api, "_cached", return_value=None),
            patch.object(capture_api, "_remember"),
            patch.object(capture_api, "_response_cache_key", return_value=("test",)),
        ]
        for active in self.patches:
            active.start()

    def tearDown(self):
        for active in reversed(self.patches):
            active.stop()
        self.db.close()
        self.engine.dispose()

    def latest(self, rows):
        self.client.creator_page.return_value = {
            "page": {"pn": 1, "ps": 30, "count": 344}, "list": {"vlist": rows}}
        return capture_api.latest_page(self.db, self.creator.id, self.account.id, 1, "all", refresh=True)

    def capture(self, **changes):
        body = capture_api.CreatorCaptureInput(account_id=self.account.id, bvids=[BVID], paid="all", **changes)
        return capture_api.capture(self.creator.id, body, Response(), user=self.user, db=self.db)

    def context(self, **policy):
        return SimpleNamespace(db=self.db, client=self.client,
            policy={"capture_creator_uid": UID, "capture_paid_filter": "all", **policy},
            cp={}, run=SimpleNamespace(id="run"), image=Mock(), save=self.db.flush)

    def video(self):
        video = Video(bvid=BVID, title="Before capture")
        self.db.add(video)
        self.db.flush()
        return video

    def test_one_joint_submission_does_not_break_a_30_video_page(self):
        rows = [listing_row(bvid=f"BV{index:010d}", aid=index + 1, mid=int(UID), is_union_video=0)
                for index in range(29)] + [listing_row()]
        result = self.latest(rows)
        self.assertEqual(len(result["items"]), 30)
        self.assertEqual(result["items"][-1]["bvid"], BVID)
        self.assertEqual((result["total"], result["next_page"]), (344, 2))

    def test_unrelated_non_union_row_is_still_rejected(self):
        for flag in (0, False, "1", 1.0):
            with self.subTest(flag=flag), self.assertRaises(IngestError) as caught:
                self.latest([listing_row(is_union_video=flag)])
            self.assertEqual(caught.exception.code, "invalid_response")

    def test_authoritative_staff_membership_can_enqueue_capture(self):
        def enqueue(*args, **kwargs):
            self.assertEqual(args[4]["capture_creator_uid"], UID)
            return SimpleNamespace(id="job", status="queued", dedupe_key=kwargs["dedupe_key"])
        with patch.object(capture_api, "enqueue", side_effect=enqueue) as queued:
            result = self.capture()
        self.assertEqual(result["queued"], 1)
        queued.assert_called_once()

    def test_capture_rejects_large_synchronous_batch_before_source_requests(self):
        with self.assertRaisesRegex(ValidationError, "每次最多提交 3 个视频"):
            capture_api.CreatorCaptureInput(account_id=self.account.id,
                bvids=[f"BV{index:010d}" for index in range(30)])
        self.client.view.assert_not_called()

    def test_rejection_mid_batch_does_not_partially_enqueue_unvalidated_selection(self):
        self.client.view.side_effect = [details(), IngestError("cooldown", code="rate_limited")]
        with patch.object(capture_api, "enqueue") as queued, self.assertRaises(IngestError):
            body = capture_api.CreatorCaptureInput(account_id=self.account.id,
                bvids=[BVID, "BV0000000001"], paid="all")
            capture_api.capture(self.creator.id, body, Response(), user=self.user, db=self.db)
        queued.assert_not_called()

    def test_original_owner_can_still_enqueue_without_staff(self):
        self.client.view.return_value = {**details(), "owner": {"mid": int(UID)}, "staff": None}
        with patch.object(capture_api, "enqueue", side_effect=lambda *a, **kw:
                SimpleNamespace(id="job", status="queued", dedupe_key=kw["dedupe_key"])):
            self.assertEqual(self.capture()["queued"], 1)

    def test_union_hint_cannot_authorize_unrelated_or_malformed_staff(self):
        for staff in ([], [{"mid": 99}], {"mid": UID}, [None, "184904514"]):
            with self.subTest(staff=staff), patch.object(capture_api, "enqueue") as queued:
                self.client.view.return_value = {**details(), "staff": staff, "is_union_video": 1}
                with self.assertRaises(HTTPException) as caught:
                    self.capture()
                self.assertEqual(caught.exception.status_code, 422)
                queued.assert_not_called()

    def test_worker_accepts_staff_and_saves_the_correct_creator_role(self):
        video, ctx = self.video(), self.context()
        runner._metadata(ctx, video)
        roles = list(self.db.scalars(select(VideoCreator.role).where(
            VideoCreator.video_id == video.id, VideoCreator.creator_id == self.creator.id)))
        self.assertEqual(roles, ["staff"])
        self.assertTrue(ctx.cp["metadata_done"])
        self.assertEqual(video.title, "Joint submission")

    def test_worker_corrects_legacy_owner_without_removing_other_roles(self):
        video, ctx = self.video(), self.context()
        self.db.add_all([VideoCreator(video_id=video.id, creator_id=self.creator.id, role=role)
                         for role in ("owner", "arranger")])
        self.db.flush()
        runner._metadata(ctx, video)
        roles = set(self.db.scalars(select(VideoCreator.role).where(
            VideoCreator.video_id == video.id, VideoCreator.creator_id == self.creator.id)))
        self.assertEqual(roles, {"staff", "arranger"})
        owner_uids = list(self.db.scalars(select(PlatformUser.uid).join(Creator, Creator.user_id == PlatformUser.id)
            .join(VideoCreator, VideoCreator.creator_id == Creator.id)
            .where(VideoCreator.video_id == video.id, VideoCreator.role == "owner")))
        self.assertEqual(owner_uids, [OWNER_UID])

    def test_worker_rechecks_staff_after_capture_was_queued(self):
        self.client.view.return_value = {**details(), "staff": [], "is_union_video": 1}
        video, ctx = self.video(), self.context()
        with self.assertRaises(IngestError) as caught:
            runner._metadata(ctx, video)
        self.assertEqual(caught.exception.code, "creator_changed")
        self.assertEqual(video.title, "Before capture")
        self.assertNotIn("metadata_done", ctx.cp)

    def test_worker_honors_participant_deletion_after_capture_was_queued(self):
        self.db.add(Setting(key=f"library_deleted_creator:{UID}", value={"deleted": True}))
        self.db.flush()
        video, ctx = self.video(), self.context()
        with self.assertRaises(IngestError) as caught:
            runner._metadata(ctx, video)
        self.assertEqual(caught.exception.code, "library_deleted")
        self.assertEqual(video.title, "Before capture")
        self.assertNotIn("metadata_done", ctx.cp)

    def test_joint_submission_still_obeys_paid_filter_at_worker(self):
        self.client.view.return_value = {**details(), "is_upower_exclusive": True}
        with self.assertRaises(IngestError) as caught:
            runner._metadata(self.context(capture_paid_filter="exclude"), self.video())
        self.assertEqual(caught.exception.code, "capture_filter_changed")

    def test_source_scanner_records_collaborator_as_staff(self):
        collection = Collection(kind="creator", source_id=UID, title="Participant")
        self.db.add(collection)
        self.db.flush()
        ctx = self.context(archive=False)
        scan = {"counts": {key: 0 for key in source_monitoring.COUNTS}, "creator_id": self.creator.id,
            "baseline_started_at": "2024-01-01T00:00:00+00:00", "initial_strategy": "all", "mode": "initial"}
        source_monitoring._observe(ctx, collection, scan, listing_row(), "2:4242", 0,
            datetime.now(timezone.utc), Mock())
        self.db.flush()
        roles = list(self.db.scalars(select(VideoCreator.role).where(VideoCreator.creator_id == self.creator.id)))
        self.assertEqual(roles, ["staff"])

    def test_source_scanner_corrects_legacy_owner_without_removing_other_roles(self):
        video = self.video()
        self.db.add_all([VideoCreator(video_id=video.id, creator_id=self.creator.id, role=role)
                         for role in ("owner", "arranger")])
        self.db.flush()
        collection = Collection(kind="creator", source_id=UID, title="Participant")
        self.db.add(collection)
        self.db.flush()
        scan = {"counts": {key: 0 for key in source_monitoring.COUNTS}, "creator_id": self.creator.id,
            "baseline_started_at": "2024-01-01T00:00:00+00:00", "initial_strategy": "all", "mode": "initial"}
        source_monitoring._observe(self.context(archive=False), collection, scan, listing_row(), "2:4242", 0,
            datetime.now(timezone.utc), Mock())
        self.db.flush()
        roles = set(self.db.scalars(select(VideoCreator.role).where(
            VideoCreator.video_id == video.id, VideoCreator.creator_id == self.creator.id)))
        self.assertEqual(roles, {"staff", "arranger"})

    def test_source_scanner_rejects_unrelated_non_union_before_observing(self):
        self.client.creator_page.return_value = {"page": {"pn": 1, "ps": 30, "count": 1},
            "list": {"vlist": [listing_row(is_union_video=0)]}}
        collection = Collection(kind="creator", source_id=UID)
        with self.assertRaises(IngestError) as caught:
            source_monitoring._fetch(self.context(), collection, 1)
        self.assertEqual(caught.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
