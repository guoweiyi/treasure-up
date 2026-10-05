"""Read projections of capture progress; Job is authoritative over saved hints.

Do not rewrite video JSON from a queue finalizer: a different lane may already
have committed newer progress. These projections also repair pre-upgrade views.
"""
from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import func, select

from app.models import Job


STOPPED = {"failed", "partial", "blocked", "cancelled", "paused"}


def latest_capture_jobs(db, videos):
    targets = {target for video in videos for target in (video.id, video.bvid) if target}
    if not targets:
        return {}
    ranked = (select(Job.id, func.row_number().over(
        partition_by=(Job.target_id, Job.kind), order_by=(Job.created_at.desc(), Job.id.desc())).label("rank"))
        .where(Job.target_id.in_(targets), Job.kind.in_(["archive_video", "download_media"])).subquery())
    by_target = {target: video.id for video in videos for target in (video.id, video.bvid) if target}
    result = defaultdict(dict)
    # A long capture's checkpoint includes comment pages, candidates and asset
    # manifests. Catalog reads need only these small status fields, not that work.
    checkpoint_keys = ("part_ids", "media_job_id", "media_parts", "media_variants", "paid_consent_required")
    policy_keys = ("download_media", "media")
    columns = [Job.id, Job.target_id, Job.kind, Job.status, Job.created_at,
               *(Job.checkpoint[key].label("checkpoint_" + key) for key in checkpoint_keys),
               *(Job.policy[key].label("policy_" + key) for key in policy_keys)]
    for row in db.execute(select(*columns).join(ranked, ranked.c.id == Job.id).where(ranked.c.rank == 1)
                          .order_by(Job.created_at, Job.id)):
        values = row._mapping
        job = SimpleNamespace(**{key: values[key] for key in ("id", "target_id", "kind", "status", "created_at")},
            checkpoint={key: values["checkpoint_" + key] for key in checkpoint_keys if values["checkpoint_" + key] is not None},
            policy={key: values["policy_" + key] for key in policy_keys if values["policy_" + key] is not None})
        result[by_target[job.target_id]][job.kind] = job
    return result


def _media_status(job):
    if (job.checkpoint or {}).get("paid_consent_required") and job.status == "succeeded":
        return "awaiting_consent"
    return "complete" if job.status == "succeeded" else job.status


def _media_required(policy):
    # Job stores the public schema; Context maps it to the internal name only
    # while executing. Match Context's public-key precedence if both exist.
    policy = policy or {}
    return bool(policy.get("download_media", policy.get("media", True)))


def project_capture(video, parts, variants, jobs):
    state = deepcopy((video.metadata_json or {}).get("ingest_state", {}))
    capture_status = video.capture_status
    parent, child = jobs.get("archive_video"), jobs.get("download_media")
    if not parent and not child:
        return capture_status, state
    if parent:
        state["metadata"] = "ready" if parent.status == "succeeded" else parent.status
        state["metadata_job_id"] = parent.id
    required = _media_required(parent.policy if parent else child.policy)
    # A newly requested metadata capture must not inherit an unrelated older
    # child's success (possibly a different quality/policy).
    if (parent and child and child.created_at < parent.created_at
            and (parent.checkpoint or {}).get("media_job_id") != child.id):
        child = None
    if not required:
        state["media"] = "disabled"
    elif child:
        state["media"], state["media_job_id"] = _media_status(child), child.id
    elif parent and (parent.checkpoint or {}).get("paid_consent_required"):
        state["media"] = "awaiting_consent"
    elif parent:
        state["media"] = "not_started"
        state.pop("media_job_id", None)

    # A successful metadata task is not evidence of media. A successful child
    # also needs a registered original for every expected part. This is a DB
    # inventory check, not a network/filesystem probe on each catalog request.
    known_parts = {part.id for part in parts}
    expected = set(((child.checkpoint if child else parent.checkpoint) or {}).get("part_ids") or known_parts)
    stored = {variant.part_id for variant in variants if variant.kind == "archive"}
    if child and (child.checkpoint or {}).get("media_variants"):
        selected = child.checkpoint["media_variants"]
        stored = {variant.part_id for variant in variants if variant.kind == "archive"
                  and selected.get(variant.part_id) == variant.id}
    if (parent and not child and parent.status == "succeeded" and required
            and expected and expected <= set((parent.checkpoint or {}).get("media_parts", []))
            and expected <= known_parts & stored and state.get("media") != "awaiting_consent"):
        # Pre-split jobs downloaded the originals inside the metadata task.
        state["media"] = "complete"
    state["media_completed_parts"] = len(expected & known_parts & stored)
    state["media_total_parts"] = len(expected)
    if state.get("media") == "complete" and (not expected or not expected <= known_parts & stored):
        state["media"] = "missing"
    metadata_status, media_status = state.get("metadata"), state.get("media")
    if metadata_status in STOPPED or media_status in STOPPED | {"missing"}:
        capture_status = "partial"
    elif metadata_status in {"queued", "running"}:
        capture_status = "capturing"
    elif metadata_status == "ready":
        capture_status = "complete" if media_status in {"complete", "disabled"} else "metadata_ready"
    elif child and media_status != "complete":
        capture_status = "partial" if media_status == "not_started" else "metadata_ready"
    return capture_status, state


def job_capture_summaries(db, rows):
    """Admin-only child status/error, pinned to this metadata task's child."""
    parents = [row for row in rows if row.kind == "archive_video"]
    child_ids = {child_id for row in parents if (child_id := (row.checkpoint or {}).get("media_job_id")
                 or (row.result or {}).get("media_job_id"))}
    children = {job.id: job for job in db.scalars(select(Job).where(Job.id.in_(child_ids)))} if child_ids else {}
    result = {}
    for row in parents:
        child_id = (row.checkpoint or {}).get("media_job_id") or (row.result or {}).get("media_job_id")
        child = children.get(child_id)
        if child and (child.kind != "download_media" or
                      (child.checkpoint or {}).get("metadata_parent_id") not in {None, row.id}):
            child = None
        required = _media_required(row.policy)
        media = "disabled" if not required else _media_status(child) if child else "not_started"
        if required and not child and (row.checkpoint or {}).get("paid_consent_required"):
            media = "awaiting_consent"
        result[row.id] = {
            "metadata": "ready" if row.status == "succeeded" else row.status,
            "media": media, "media_job_id": child.id if child else None,
            "media_error": child.error if child else None,
            "retry_media": bool(child and child.status in {"failed", "partial", "blocked", "cancelled"}),
            "resume_media": bool(child and child.status == "paused"),
        }
    return result
