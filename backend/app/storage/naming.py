"""Readable immutable media keys; non-media assets retain their existing layout."""
from dataclasses import dataclass
import re
import unicodedata

from sqlalchemy import select

from app.models import Creator, MediaVariant, PlatformUser, Video, VideoCreator, VideoPart
from .base import StorageError, content_key, safe_key


_EXTENSIONS = {"video/mp4": "mp4", "video/x-matroska": "mkv", "video/webm": "webm",
               "video/x-flv": "flv", "video/quicktime": "mov"}


def _component(value, budget, fallback):
    value = unicodedata.normalize("NFC", str(value or ""))
    value = "".join(character if (character in "._- []()" or unicodedata.category(character)[0] in "LNM"
                                 or unicodedata.category(character) == "So") else "-" for character in value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    value = value.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .-")
    return value or fallback


@dataclass(frozen=True)
class MediaName:
    title: str = ""
    creator: str = ""
    bvid: str = ""
    position: int = 1
    part_title: str = ""
    variant: str = "archive"

    def key(self, sha, mime_type):
        content_key(sha)  # Reject an invalid digest before building a filename.
        title = _component(self.title, 48, "未命名视频")
        creator = _component(self.creator, 30, "未知UP主")
        identity = _component(self.bvid or "local_" + sha[:24], 32, "local_" + sha[:24])
        position = min(100000, max(1, int(self.position or 1)))
        part = _component(self.part_title, 24, "") if self.part_title and self.part_title != self.title else ""
        suffix = "-播放副本" if self.variant == "playback" else ""
        part_name = f"-{part}" if part else ""
        extension = _EXTENSIONS.get(mime_type, "media")
        # A single shallow directory avoids repeating long titles on Windows.
        # The full digest binds immutable ownership, including different encodes.
        return safe_key(f"videos/{title}-{creator} [{identity}]-P{position:02d}{part_name}{suffix} [{sha}].{extension}")


def is_managed_key(key, sha):
    """Only our immutable namespace may be deleted, migrated or restored."""
    try:
        safe_key(key)
        if key == content_key(sha):
            return True
        return bool(re.fullmatch(r"videos/[^/]+ \[" + re.escape(sha) + r"\]\.(?:mp4|mkv|webm|flv|mov|media)", key))
    except (StorageError, TypeError):
        return False


def media_name_for_part(db, part, *, video=None, variant="archive"):
    video = video or db.get(Video, part.video_id)
    if not video:
        return None
    creator = db.scalar(select(PlatformUser.display_name).join(Creator, Creator.user_id == PlatformUser.id)
        .join(VideoCreator, VideoCreator.creator_id == Creator.id)
        .where(VideoCreator.video_id == video.id, VideoCreator.role == "owner")
        .order_by(VideoCreator.id).limit(1))
    owner = (video.metadata_json or {}).get("owner") or {}
    creator = creator or (owner.get("name") if isinstance(owner, dict) else "")
    return MediaName(video.title, creator or "", video.bvid, part.position, part.title, variant)


def media_name_for_asset(db, asset):
    if not asset.mime_type.startswith("video/") or asset.kind not in {"media", "video"}:
        return None
    row = db.execute(select(MediaVariant, VideoPart).join(VideoPart, VideoPart.id == MediaVariant.part_id)
        .where(MediaVariant.asset_id == asset.id, MediaVariant.kind.in_(["archive", "playback"]))
        .order_by((MediaVariant.kind == "archive").desc(), MediaVariant.created_at, MediaVariant.id).limit(1)).first()
    return media_name_for_part(db, row[1], variant=row[0].kind) if row else None
