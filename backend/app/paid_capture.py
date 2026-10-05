"""Source-scoped opt-in for paid media; never overrides upstream entitlement."""
from sqlalchemy import case, func, or_, select

from app.ingest.errors import IngestError
from app.models import CaptureRun, CollectionItem, Job, MediaVariant, SourceSubscription, Video, VideoPart


class PaidConsentRequired(IngestError):
    def __init__(self):
        super().__init__("来源发现充电视频，等待用户确认保存", code="paid_consent_required", retryable=False)


def paid_hint(raw):
    # Only explicit flags are evidence. Text such as “充电” in a title is not.
    return isinstance(raw, dict) and any(raw.get(key) is True or raw.get(key) == 1
        for key in ("is_upower_exclusive", "is_charging_arc"))


def is_paid(video):
    metadata = video.metadata_json or {}
    return paid_hint(metadata) or (metadata.get("access") or {}).get("upower_exclusive") is True


def consent(db, collection_id, account_id=None):
    # Read the saved source, not global defaults or a previously frozen job.
    # Account replacement requires a new job using the selected account.
    source = db.scalar(select(SourceSubscription).where(SourceSubscription.collection_id == collection_id)
                       .execution_options(populate_existing=True))
    return bool(source and (account_id is None or source.account_id == account_id)
                and (source.policy or {}).get("include_paid_videos") is True)


def source_origin(db, job):
    origin = (job.policy or {}).get("source_collection_id")
    if origin:
        return str(origin)
    # Recover source identity for already queued jobs created before this field
    # existed. Direct single-video requests have a different demand key.
    if job.kind == "download_media":
        parent_id = (job.checkpoint or {}).get("metadata_parent_id")
        parent = db.get(Job, parent_id) if parent_id else None
        if parent and parent.kind == "archive_video":
            return source_origin(db, parent)
    if job.kind == "archive_video" and (job.dedupe_key or "").startswith("archive:"):
        run = db.get(CaptureRun, job.dedupe_key.rsplit(":", 1)[-1])
        if run:
            return run.collection_id
    return None


def summaries(db, collection_ids):
    """Late view detections are included without rewriting a scanner's JSON."""
    paid = or_(CollectionItem.observation["paid"].as_boolean().is_(True),
        Video.metadata_json["is_upower_exclusive"].as_boolean().is_(True),
        Video.metadata_json["access"]["upower_exclusive"].as_boolean().is_(True))
    archive_exists = select(MediaVariant.id).join(VideoPart, VideoPart.id == MediaVariant.part_id).where(
        VideoPart.video_id == Video.id, MediaVariant.kind == "archive").exists()
    saved = or_(Video.metadata_json["ingest_state"]["media"].as_string() == "complete",
                (Video.capture_status == "complete") & archive_exists)
    rows = db.execute(select(CollectionItem.collection_id, func.count(),
            func.sum(case((saved, 0), else_=1))).join(Video, Video.id == CollectionItem.video_id)
        .where(CollectionItem.collection_id.in_(collection_ids), paid,
               CollectionItem.source_state != "deleted_locally").group_by(CollectionItem.collection_id))
    return {identifier: {"detected_count": detected, "pending_count": pending} for identifier, detected, pending in rows}


def summary(db, collection_id, policy, counts=None):
    found = (summaries(db, [collection_id]) if counts is None else counts).get(collection_id, {})
    result = {"detected_count": found.get("detected_count", 0), "pending_count": found.get("pending_count", 0)}
    result["consent_required"] = result["pending_count"] > 0 and (policy or {}).get("include_paid_videos") is not True
    return result
