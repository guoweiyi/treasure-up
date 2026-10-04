"""Bounded hot-comment sampling. Ranking is only over the observed sample.

Existing inventory is preserved: a refresh updates saved comments and fills
free slots, but never deletes history or silently grows beyond the policy cap.
"""
import hashlib
import json

from sqlalchemy import func, select

from app.models import Comment, CommentVersion
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
    return clean_raw(saved)


def _rank(raw):
    return (max(0, int(raw.get("like") or 0)), int(raw.get("ctime") or 0), int(identity(raw)))


def _save_state(ctx, state):
    ctx.cp["comments"] = dict(state)
    ctx.save()


def _finish(ctx, video, state):
    video.metadata_json = {**(video.metadata_json or {}), "comment_capture": {
        "run_id": ctx.run.id, "ranking": "likes_within_bounded_sample", "requested_order": "hot", "retention": "preserve_existing",
        "scanned_roots": state.get("scanned", 0), "selected_roots": len(state.get("selected", [])),
        "source_end_observed": state.get("source_end", False), "end_reason": state.get("end_reason", "source_end"),
        "replies_limited": state.get("replies_limited", False),
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
    candidates = dict(state.get("candidates", {}))
    offsets = list(state.get("seen_offsets", []))
    fingerprints = list(state.get("root_fingerprints", []))
    while not state.get("roots_done"):
        if not ctx.request_slot():
            return False
        offset = state.get("offset", "")
        data = ctx.client.comment_page(video.aid, offset)
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
        for raw in pinned(data) + rows:
            rid = identity(raw)
            if rid not in seen:
                if len(seen) >= scan:
                    break
                seen.add(rid)
            candidates[rid] = compact(raw)
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
        # Existing rows count toward inventory, even if they fall outside this
        # response sample. Legacy inventories above the new cap are not deleted.
        existing = set(ctx.db.scalars(select(Comment.rpid).where(Comment.video_id == video.id, Comment.root_rpid == "0")))
        free = max(0, top - len(existing))
        chosen = []
        for raw in sorted(candidates.values(), key=_rank, reverse=True):
            rid = identity(raw)
            if rid not in existing:
                if not free:
                    continue
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
