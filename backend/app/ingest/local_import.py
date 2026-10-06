"""Import an explicitly selected local file without contacting Bilibili."""
import json
import math
import re
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.models import (CaptureRun, Creator, DanmakuSnapshot, MediaVariant,
    PlatformUser, UserSnapshot, Video, VideoCreator, VideoPart)
from app.storage.service import ingest_file
from app.storage.naming import MediaName
from .errors import IngestError
from .media import _probe
from .runner import _now, _ref


def _platform_id(value, label):
    if value is None:
        return None
    value = str(value)
    if not value.isdecimal() or int(value) <= 0 or len(value) > 32:
        raise IngestError(f"本地导入的 {label} 无效", code="invalid_import", retryable=False)
    return value


def _danmaku_data(path, mode_space):
    if path.stat().st_size > 64 * 1024**2:
        raise IngestError("弹幕导入文件超过大小限制", code="invalid_import", retryable=False)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        raise IngestError("本地弹幕不是合法 JSON", code="invalid_import", retryable=False) from None
    if isinstance(data, dict):
        data = data.get("items", data.get("danmaku"))
    if not isinstance(data, list) or len(data) > 500000 or mode_space not in ("bilibili", "artplayer"):
        raise IngestError("本地弹幕结构或模式空间无效", code="invalid_import", retryable=False)
    normalized = []
    for index, item in enumerate(data):
        try:
            seconds, mode = float(item["time"]), int(item.get("mode", 1 if mode_space == "bilibili" else 0))
            text, color = item["text"], str(item.get("color", "#ffffff"))
            size = float(item.get("size", item.get("fontSize", 25)))
            if not isinstance(text, str) or not math.isfinite(seconds) or seconds < 0 or not math.isfinite(size) or not 0 < size <= 1000 or not re.fullmatch(r"#[a-fA-F0-9]{6}", color):
                raise ValueError()
            if mode_space == "artplayer":
                mode = {0: 1, 1: 5, 2: 4}[mode]
            if not 1 <= mode <= 9:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise IngestError("本地弹幕含有无效条目", code="invalid_import", retryable=False) from None
        normalized.append({"id": str(item.get("id") or f"local:{index}"), "time": seconds, "text": text, "mode": mode, "color": color.lower(), "size": size})
    return sorted(normalized, key=lambda item: item["time"])


def import_local(db, path, metadata, danmaku_path=None):
    """Stage ORM changes; caller owns the transaction. No source requests.

    metadata: bvid?, aid?, title?, description?, cid?, part_title?, position?,
    owner?: {uid|mid, name, signature?}, danmaku_mode_space?: bilibili|artplayer.
    Missing source IDs receive explicit deterministic local_ identifiers.
    """
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise IngestError("本地媒体必须是普通文件", code="invalid_import", retryable=False)
    if not isinstance(metadata, dict):
        raise IngestError("本地导入元数据必须是对象", code="invalid_import", retryable=False)
    bvid = metadata.get("bvid")
    if bvid is not None and not re.fullmatch(r"BV[A-Za-z0-9]{10}", str(bvid)):
        raise IngestError("本地导入的 BV 号无效", code="invalid_import", retryable=False)
    aid, cid = _platform_id(metadata.get("aid"), "AID"), _platform_id(metadata.get("cid"), "CID")
    owner = metadata.get("owner")
    uid = _platform_id(owner.get("uid", owner.get("mid")), "UID") if isinstance(owner, dict) else None
    try:
        position = int(metadata.get("position", 1))
        if position < 1 or position > 100000:
            raise ValueError()
    except (TypeError, ValueError):
        raise IngestError("本地分P序号无效", code="invalid_import", retryable=False) from None
    vstream, astream, duration = _probe(path)
    normalized, dm_path = None, None
    if danmaku_path is not None:
        dm_path = Path(danmaku_path)
        if not dm_path.is_file() or dm_path.is_symlink():
            raise IngestError("本地弹幕必须是普通文件", code="invalid_import", retryable=False)
        normalized = _danmaku_data(dm_path, metadata.get("danmaku_mode_space", "bilibili"))
    mime = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm", ".flv": "video/x-flv", ".mov": "video/quicktime"}.get(path.suffix.lower(), "application/octet-stream")
    asset = ingest_file(db, path, kind="media", mime_type=mime, media_name=MediaName(
        title=str(metadata.get("title") or path.stem), creator=str(owner.get("name") or "") if isinstance(owner, dict) else "",
        bvid=bvid or "", position=position, part_title=str(metadata.get("part_title") or "")))
    identity = "local_" + asset.sha256[:24]
    bvid, cid = bvid or identity, cid or identity
    video = db.scalar(select(Video).where(Video.bvid == bvid))
    if video is None:
        video = Video(bvid=bvid, aid=aid, source_state="local_import", capture_status="local_import")
        db.add(video)
        db.flush()
    if aid:
        video.aid = aid
    video.title = str(metadata.get("title") or video.title or path.stem)
    video.description = str(metadata.get("description", video.description or ""))
    video.duration = max(video.duration or 0, duration)
    video.metadata_json = {**(video.metadata_json or {}), "local_import": {"observed_at": _now().isoformat(), "sha256": asset.sha256, "source_identified": not bvid.startswith("local_")}}
    part = db.scalar(select(VideoPart).where(VideoPart.video_id == video.id, VideoPart.cid == cid))
    if not part:
        part = VideoPart(video_id=video.id, cid=cid)
        db.add(part)
        db.flush()
    part.position, part.title, part.duration = position, str(metadata.get("part_title") or video.title), duration
    key = "local:" + asset.sha256
    variant = db.scalar(select(MediaVariant).where(MediaVariant.part_id == part.id, MediaVariant.format_key == key, MediaVariant.kind == "archive"))
    if not variant:
        width, height = int(vstream.get("width") or 0), int(vstream.get("height") or 0)
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind="archive", format_key=key,
            quality=str(min(width, height) or "local"), width=width, height=height,
            video_codec=vstream.get("codec_name") or "", audio_codec=astream.get("codec_name") or "", duration=duration)
        db.add(variant)
        db.flush()
    _ref(db, asset.id, "media_variant", variant.id, "archive")
    if uid:
        user = db.scalar(select(PlatformUser).where(PlatformUser.uid == uid))
        if not user:
            user = PlatformUser(uid=uid, display_name=str(owner.get("name") or ""), signature=str(owner.get("signature") or ""), raw={"source": "local_import"})
            db.add(user); db.flush()
            db.add(UserSnapshot(user_id=user.id, display_name=user.display_name, signature=user.signature, observed_at=_now()))
        creator = db.scalar(select(Creator).where(Creator.user_id == user.id))
        if not creator:
            creator = Creator(user_id=user.id)
            db.add(creator); db.flush()
        if not db.scalar(select(VideoCreator).where(VideoCreator.video_id == video.id, VideoCreator.creator_id == creator.id, VideoCreator.role == "owner")):
            db.add(VideoCreator(video_id=video.id, creator_id=creator.id, role="owner", role_title="UP主"))
    run = CaptureRun(video_id=video.id, status="complete", scope={"kind": "local_import", "media": True, "danmaku": normalized is not None}, counts={"parts": 1, "danmaku": len(normalized or [])}, end_reason="local_files_verified", finished_at=_now())
    db.add(run); db.flush()
    if normalized is not None:
        raw_asset = ingest_file(db, dm_path, kind="danmaku_raw", mime_type="application/json")
        settings.scratch_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="local-danmaku-", dir=settings.scratch_dir) as directory:
            converted = Path(directory) / "danmaku.json"
            converted.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
            data_asset = ingest_file(db, converted, kind="danmaku", mime_type="application/json")
        snapshot = DanmakuSnapshot(part_id=part.id, run_id=run.id, status="imported", expected_segments=0, completed_segments=0, raw_asset_ids=[raw_asset.id], data_asset_id=data_asset.id, count=len(normalized))
        db.add(snapshot); db.flush()
        _ref(db, raw_asset.id, "danmaku_snapshot", snapshot.id, "raw")
        _ref(db, data_asset.id, "danmaku_snapshot", snapshot.id, "playback")
    db.flush()
    return video
