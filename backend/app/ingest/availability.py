"""Conservative source evidence. A missing source never removes its archive."""
from sqlalchemy import select

from app.models import CollectionItem, utcnow
from app.paid_capture import is_paid

VIEW_ENDPOINTS = {"/x/web-interface/view", "/x/web-interface/wbi/view"}
HISTORY_LIMIT = 20


def observe(ctx, video, *, error=None):
    previous = dict(video.source_availability or {})
    if error is None:
        outcome, reason = "available", "view_confirmed"
    else:
        paid = is_paid(video) or bool(ctx.db.scalar(select(CollectionItem.id).where(
            CollectionItem.video_id == video.id, CollectionItem.observation["paid"].as_boolean().is_(True)).limit(1)))
        missing = (error.code == "not_found" and error.source_code == -404
                   and error.source_endpoint in VIEW_ENDPOINTS)
        outcome = "unavailable" if missing and not paid else "inconclusive"
        reason = "paid_access_unconfirmed" if missing and paid else error.code
    observation = {"checked_at": utcnow().isoformat(), "outcome": outcome, "reason": reason,
                   "run_id": ctx.run.id}
    if error and error.source_endpoint in VIEW_ENDPOINTS and type(error.source_code) is int:
        observation["source_code"] = error.source_code
    history = list(previous.get("history") or [])
    # One record per run, even after a retry; bounded metadata cannot grow with
    # every periodic refresh. CaptureRun still retains the task-level result.
    history = [item for item in history if isinstance(item, dict) and item.get("run_id") != ctx.run.id]
    history.append(observation)
    if outcome in {"available", "unavailable"}:
        video.source_state = outcome
        previous["last_confirmed_at"] = observation["checked_at"]
    video.source_availability = {**previous, **observation, "history": history[-HISTORY_LIMIT:]}
    ctx.cp["source_check"] = observation
    return outcome
