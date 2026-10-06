"""Bounded hot-comment sampling. Ranking is only over the observed sample.

Ordinary refreshes preserve the inventory. New pins take precedence within the
same root limit, replacing the lowest-liked ordinary root and its reply tree.
Shared people/assets and the cumulative downloaded-material budget survive.
"""
import hashlib
import json

from sqlalchemy import delete, func, select, update

from app.models import AssetRef, Comment, CommentAsset, CommentVersion
from .client import clean_raw
from .errors import IngestError


DEFAULTS = {"comment_top_limit": 500, "comment_scan_limit": 2000,
    "comment_reply_total_limit": 200, "comment_reply_per_root_limit": 10,
    "comment_asset_count_limit": 300, "comment_asset_bytes_limit": 25 * 1024**2}
MAXIMA = {"comment_top_limit": 10000, "comment_scan_limit": 100000,
    "comment_reply_total_limit": 100000, "comment_reply_per_root_limit": 1000,
    "comment_asset_count_limit": 10000, "comment_asset_bytes_limit": 1024**3}


def budget(policy, key):
    return max(0, min(MAXIMA[key], int(policy.get(key, DEFAULTS[key]))))


def identity(raw):
    result = str(raw.get("rpid_str", raw.get("rpid", "")))
    if not result.isascii() or not result.isdecimal() or len(result) > 32 or int(result) < 1:
        raise IngestError("评论标识无效", code="invalid_comments")
    return result


def compact(raw):
    """No nested reply previews or unrelated upstream blobs in checkpoints."""
    content = raw.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("message"), str):
        raise IngestError("评论正文结构无效", code="invalid_comments")
    if len(content["message"]) > 50000:
        raise IngestError("评论正文超过允许大小", code="invalid_comments")
    member = raw.get("member")
    saved = {key: raw[key] for key in ("rpid", "rpid_str", "oid", "root", "root_str", "parent", "parent_str", "ctime", "like", "rcount", "count") if key in raw}
    saved["content"] = {"message": content["message"],
        "pictures": [{"img_src": p.get("img_src")} for p in (content.get("pictures") or [])[:20] if isinstance(p, dict)],
        "emote": {k: {"url": v.get("url")} for k, v in list((content.get("emote") or {}).items())[:100] if isinstance(v, dict)}}
    saved["member"] = {key: member[key] for key in ("mid", "uname", "name", "sign", "avatar", "face") if key in member} if isinstance(member, dict) else None
    saved["is_pinned"] = raw.get("is_pinned") is True
    return clean_raw(saved)


def _rank(raw):
    return (raw.get("is_pinned") is True, max(0, int(raw.get("like") or 0)), int(raw.get("ctime") or 0), int(identity(raw)))


def _make_pin_room(ctx, video, protected):
    """Replace one low-ranked ordinary root only when a new pin needs its slot.

    People and immutable assets can be shared and must survive. Detach comment
    refs and versions only; the existing cumulative material budget is retained,
    so repeated pin changes cannot download an unbounded new image inventory.
    """
    victim = ctx.db.scalar(select(Comment).where(Comment.video_id == video.id, Comment.root_rpid == "0",
        Comment.rpid.not_in(protected), Comment.is_pinned.is_(False))
        .order_by(Comment.like_count, Comment.posted_at, Comment.id).limit(1))
    if victim is None:
        return False
    # Initialize legacy material accounting before detaching its last comment
    # references; otherwise an upgrade plus pin replacement could free budget
    # for files which still occupy storage and are intentionally retained.
    from .runner import _comment_ledger
    _comment_ledger(ctx, video)
    ids = list(ctx.db.scalars(select(Comment.id).where(Comment.video_id == video.id,
        (Comment.id == victim.id) | (Comment.root_rpid == victim.rpid))))
    ctx.db.execute(delete(AssetRef).where(AssetRef.entity_type == "comment", AssetRef.entity_id.in_(ids)))
    ctx.db.execute(delete(CommentAsset).where(CommentAsset.comment_id.in_(ids)))
    ctx.db.execute(delete(CommentVersion).where(CommentVersion.comment_id.in_(ids)))
    ctx.db.execute(delete(Comment).where(Comment.id.in_(ids)))
    ctx.db.flush()
    return victim.rpid


def _save_state(ctx, state):
    ctx.cp["comments"] = dict(state)
    ctx.save()


def _finish(ctx, video, state):
    video.metadata_json = {**(video.metadata_json or {}), "comment_capture": {
        "run_id": ctx.run.id, "ranking": "pins_then_likes_within_bounded_sample", "requested_order": "hot",
        "retention": "preserve_existing_except_pin_priority",
        "scanned_roots": state.get("scanned", 0), "selected_roots": len(state.get("selected", [])),
        "source_end_observed": state.get("source_end", False), "end_reason": state.get("end_reason", "source_end"),
        "replies_limited": state.get("replies_limited", False),
        "pinned_roots": len(state.get("pinned_ids", [])), "pin_replacements": state.get("pin_replacements", 0),
        "limits": {key: budget(ctx.policy, key) for key in DEFAULTS},
        "roots_in_inventory": ctx.db.scalar(select(func.count()).select_from(Comment).where(Comment.video_id == video.id, Comment.root_rpid == "0")),
        "replies_in_inventory": ctx.db.scalar(select(func.count()).select_from(Comment).where(Comment.video_id == video.id, Comment.root_rpid != "0"))}}
    _save_state(ctx, state)
    return True


def collect(ctx, video, save_comment, pinned):
    state = dict(ctx.cp.get("comments", {}))
    top, scan = budget(ctx.policy, "comment_top_limit"), budget(ctx.policy, "comment_scan_limit")
    total_replies, per_root = budget(ctx.policy, "comment_reply_total_limit"), budget(ctx.policy, "comment_reply_per_root_limit")
    if top == 0 or scan == 0:
        state.update(roots_done=True, replies_done=True, end_reason="policy_disabled")
        return _finish(ctx, video, state)
    seen = set(state.get("seen_ids", []))
    if "pinned_ids" not in state:
        state["pinned_ids"] = list(ctx.db.scalars(select(Comment.rpid).where(
            Comment.video_id == video.id, Comment.is_pinned.is_(True)).limit(3)))
    candidates = dict(state.get("candidates", {}))
    offsets = list(state.get("seen_offsets", []))
    fingerprints = list(state.get("root_fingerprints", []))
    while not state.get("roots_done"):
        if not ctx.request_slot():
            return False
        offset = state.get("offset", "")
        try:
            data = ctx.client.comment_page(video.aid, offset)
        except IngestError as error:
            if error.code != "comments_closed":
                raise
            # A disabled comment section is a terminal source state, not a
            # broken capture. Keep the existing archive and previous pin state.
            state.update(roots_done=True, replies_done=True, end_reason="comments_closed")
            return _finish(ctx, video, state)
        cursor, rows = data.get("cursor"), data.get("replies")
        if not isinstance(cursor, dict) or not isinstance(cursor.get("is_end"), bool):
            raise IngestError("评论分页缺少合法结束标记", code="invalid_pagination")
        if cursor.get("mode", 3) not in (2, 3):
            raise IngestError("评论排序模式不受支持", code="unsupported_comment_order", retryable=False)
        if rows is None and cursor["is_end"]:
            rows = []
        if not isinstance(rows, list) or (not rows and not cursor["is_end"]):
            raise IngestError("评论分页提前返回空页", code="invalid_pagination")
        nxt = (cursor.get("pagination_reply") or {}).get("next_offset")
        page_ids = [identity(raw) for raw in rows]
        fingerprint = hashlib.sha256(json.dumps(sorted(set(page_ids))).encode()).hexdigest()
        if not cursor["is_end"] and (not isinstance(nxt, str) or not nxt):
            raise IngestError("评论游标缺失，已保留上一检查点", code="pagination_loop")
        # Some valid hot-comment responses reuse one opaque next_offset while
        # returning a different page. Progress is new reply IDs, not token text.
        # This also works with older checkpoints that only contain seen_ids.
        if not cursor["is_end"] and (not set(page_ids).difference(seen) or fingerprint in fingerprints):
            raise IngestError("评论游标未产生新评论或重复返回同页，已保留检查点", code="pagination_loop")
        page_pins = pinned(data)[:3]
        if not offset and ("top_replies" in data or "top" in data):
            state["pinned_ids"] = [identity(raw) for raw in page_pins]
            state["pin_state_observed"] = True
        pin_ids = set(state.get("pinned_ids", []))
        for raw in page_pins + rows:
            rid = identity(raw)
            if rid not in seen:
                if len(seen) >= scan:
                    break
                seen.add(rid)
            candidates[rid] = compact({**raw, "is_pinned": rid in pin_ids or raw.get("is_pinned") is True})
            # Candidates are raw only until selection, bounded independently of
            # scan size. No users, avatar tasks or comments are created here.
            if len(candidates) > top:
                del candidates[min(candidates, key=lambda key: _rank(candidates[key]))]
        # Duplicate pages with changing opaque cursors must not extend a scan
        # indefinitely. Examined rows, including repeats, also consume budget.
        state.update(scanned=len(seen), examined=min(scan, state.get("examined", 0) + len(rows)),
                     seen_ids=sorted(seen), candidates=candidates, root_fingerprints=fingerprints + [fingerprint])
        fingerprints.append(fingerprint)
        if cursor["is_end"] or len(seen) >= scan or state["examined"] >= scan:
            state.update(roots_done=True, source_end=cursor["is_end"],
                end_reason="source_end" if cursor["is_end"] else "scan_budget")
        else:
            offsets.append(nxt)
            state.update(offset=nxt, seen_offsets=offsets)
        _save_state(ctx, state)
    if "selected" not in state:
        if state.get("pin_state_observed"):
            ctx.db.execute(update(Comment).where(Comment.video_id == video.id, Comment.is_pinned.is_(True),
                Comment.rpid.not_in(state.get("pinned_ids", []))).values(is_pinned=False))
        # Existing rows count toward inventory, even if they fall outside this
        # response sample. Legacy inventories above the new cap are not deleted.
        existing = set(ctx.db.scalars(select(Comment.rpid).where(Comment.video_id == video.id, Comment.root_rpid == "0")))
        free = max(0, top - len(existing))
        chosen = []
        for raw in sorted(candidates.values(), key=_rank, reverse=True):
            rid = identity(raw)
            if rid not in existing:
                if not free:
                    replaced = _make_pin_room(ctx, video, state.get("pinned_ids", [])) if raw.get("is_pinned") is True else None
                    if not replaced:
                        continue
                    existing.discard(replaced)
                    state["pin_replacements"] = state.get("pin_replacements", 0) + 1
                else:
                    free -= 1
            save_comment(ctx, video, raw, "0")
            chosen.append(rid)
        # Legacy checkpoints may have already committed roots before upgrade.
        if not candidates:
            chosen = list(ctx.db.scalars(select(Comment.rpid).join(CommentVersion, CommentVersion.comment_id == Comment.id)
                .where(Comment.video_id == video.id, Comment.root_rpid == "0", CommentVersion.run_id == ctx.run.id)
                .order_by(Comment.like_count.desc(), Comment.rpid.desc()).limit(top)))
        state.update(selected=chosen, candidates={}, seen_ids=[], seen_offsets=[], root_fingerprints=[])
        _save_state(ctx, state)
    while not state.get("replies_done"):
        selected = state.get("selected", [])
        index = int(state.get("reply_root_index", 0))
        if index >= len(selected) or total_replies == 0 or per_root == 0:
            state["replies_done"] = True
            break
        root = ctx.db.scalar(select(Comment).where(Comment.video_id == video.id, Comment.rpid == selected[index]))
        root_count = ctx.db.scalar(select(func.count()).select_from(Comment).where(Comment.video_id == video.id, Comment.root_rpid == root.rpid))
        total_count = ctx.db.scalar(select(func.count()).select_from(Comment).where(Comment.video_id == video.id, Comment.root_rpid != "0"))
        if not root.reply_count or root_count >= per_root or total_count >= total_replies:
            if root.reply_count > root_count:
                state["replies_limited"] = True
            state.update(reply_root_index=index + 1, reply_page=1, reply_fingerprints=[])
            _save_state(ctx, state)
            continue
        if not ctx.request_slot():
            return False
        pn = int(state.get("reply_page", 1))
        data = ctx.client.replies_page(video.aid, root.rpid, pn)
        page, rows = data.get("page"), data.get("replies")
        if not isinstance(page, dict) or not all(type(page.get(k)) is int for k in ("num", "size", "count")) or page["num"] != pn or page["size"] <= 0 or page["count"] < 0:
            raise IngestError("楼中楼分页结构异常", code="invalid_pagination")
        if rows is None and page["count"] == 0:
            rows = []
        if not isinstance(rows, list) or (not rows and page["count"] > (pn - 1) * page["size"]):
            raise IngestError("楼中楼提前返回空页", code="invalid_pagination")
        digest = hashlib.sha256(json.dumps([identity(raw) for raw in rows]).encode()).hexdigest()
        prior = list(state.get("reply_fingerprints", []))
        if rows and digest in prior:
            raise IngestError("楼中楼重复返回同一页", code="pagination_loop")
        remaining = min(per_root - root_count, total_replies - total_count)
        for raw in rows:
            exists = ctx.db.scalar(select(Comment.id).where(Comment.video_id == video.id, Comment.rpid == identity(raw)))
            if not exists:
                if remaining <= 0:
                    break
                remaining -= 1
            save_comment(ctx, video, compact(raw), root.rpid)
        if remaining <= 0 or pn * page["size"] >= page["count"]:
            if remaining <= 0 and page["count"] > root_count + min(per_root - root_count, total_replies - total_count):
                state["replies_limited"] = True
            state.update(reply_root_index=index + 1, reply_page=1, reply_fingerprints=[])
        else:
            state.update(reply_page=pn + 1, reply_fingerprints=prior + [digest])
        _save_state(ctx, state)
    return _finish(ctx, video, state)
