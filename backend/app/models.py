from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Entity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Entity, Base):
    __tablename__ = "app_users"
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="reader")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)


class UserSession(Entity, Base):
    __tablename__ = "sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginAttempt(Entity, Base):
    __tablename__ = "login_attempts"
    client_hash: Mapped[str] = mapped_column(String(64), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)


class PlatformUser(Entity, Base):
    __tablename__ = "platform_users"
    uid: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(500), default="")
    signature: Mapped[str] = mapped_column(Text, default="")
    avatar_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class UserSnapshot(Entity, Base):
    __tablename__ = "user_snapshots"
    user_id: Mapped[str] = mapped_column(ForeignKey("platform_users.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(500), default="")
    signature: Mapped[str] = mapped_column(Text, default="")
    avatar_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Creator(Entity, Base):
    __tablename__ = "creators"
    user_id: Mapped[str] = mapped_column(ForeignKey("platform_users.id"), unique=True)
    alias: Mapped[str | None] = mapped_column(String(500))
    description_override: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)


class Video(Entity, Base):
    __tablename__ = "videos"
    bvid: Mapped[str] = mapped_column(String(32), unique=True)
    aid: Mapped[str | None] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[float] = mapped_column(Float, default=0)
    cover_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    source_state: Mapped[str] = mapped_column(String(40), default="available")
    capture_status: Mapped[str] = mapped_column(String(40), default="pending")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class VideoAnnotation(Entity, Base):
    __tablename__ = "video_annotations"
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), unique=True)
    title_override: Mapped[str | None] = mapped_column(Text)
    description_override: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)


class VideoCreator(Entity, Base):
    __tablename__ = "video_creators"
    __table_args__ = (UniqueConstraint("video_id", "creator_id", "role"),)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), index=True)
    role: Mapped[str] = mapped_column(String(40), default="owner")
    role_title: Mapped[str] = mapped_column(String(100), default="UP主")


class VideoPart(Entity, Base):
    __tablename__ = "video_parts"
    __table_args__ = (UniqueConstraint("video_id", "cid"),)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    cid: Mapped[str] = mapped_column(String(32), index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[float] = mapped_column(Float, default=0)


class Collection(Entity, Base):
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("kind", "source_id"),)
    source_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(40), default="favorite")
    owner_uid: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CollectionItem(Entity, Base):
    __tablename__ = "collection_items"
    __table_args__ = (UniqueConstraint("collection_id", "source_resource_id"),)
    collection_id: Mapped[str] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), index=True)
    source_resource_id: Mapped[str] = mapped_column(String(100))
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    source_state: Mapped[str] = mapped_column(String(40), default="available")
    seen_run_id: Mapped[str | None] = mapped_column(String(36))


class Asset(Entity, Base):
    __tablename__ = "assets"
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    kind: Mapped[str] = mapped_column(String(50), default="other")


class StorageProfile(Entity, Base):
    __tablename__ = "storage_profiles"
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="local")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_encrypted: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AssetLocation(Entity, Base):
    __tablename__ = "asset_locations"
    __table_args__ = (UniqueConstraint("asset_id", "storage_profile_id", "object_key"),)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    storage_profile_id: Mapped[str] = mapped_column(ForeignKey("storage_profiles.id"), index=True)
    object_key: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(40), default="ready")
    checksum: Mapped[str | None] = mapped_column(Text)
    version_id: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetRef(Entity, Base):
    __tablename__ = "asset_refs"
    __table_args__ = (UniqueConstraint("asset_id", "entity_type", "entity_id", "purpose"),)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    purpose: Mapped[str] = mapped_column(String(50))


class MediaVariant(Entity, Base):
    __tablename__ = "media_variants"
    __table_args__ = (UniqueConstraint("part_id", "format_key", "kind"),)
    part_id: Mapped[str] = mapped_column(ForeignKey("video_parts.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    kind: Mapped[str] = mapped_column(String(40), default="archive")
    format_key: Mapped[str] = mapped_column(String(250))
    quality: Mapped[str] = mapped_column(String(100), default="原画")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    video_codec: Mapped[str] = mapped_column(String(100), default="")
    audio_codec: Mapped[str] = mapped_column(String(100), default="")
    duration: Mapped[float] = mapped_column(Float, default=0)


class DanmakuSnapshot(Entity, Base):
    __tablename__ = "danmaku_snapshots"
    part_id: Mapped[str] = mapped_column(ForeignKey("video_parts.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    expected_segments: Mapped[int] = mapped_column(Integer, default=0)
    completed_segments: Mapped[int] = mapped_column(Integer, default=0)
    raw_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    data_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    count: Mapped[int] = mapped_column(Integer, default=0)


class SubtitleTrack(Entity, Base):
    __tablename__ = "subtitle_tracks"
    __table_args__ = (UniqueConstraint("part_id", "language", "is_auto"),)
    part_id: Mapped[str] = mapped_column(ForeignKey("video_parts.id"), index=True)
    language: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(100), default="")
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))


class Comment(Entity, Base):
    __tablename__ = "comments"
    __table_args__ = (UniqueConstraint("video_id", "rpid"), Index("ix_comment_tree", "video_id", "root_rpid", "posted_at"))
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    rpid: Mapped[str] = mapped_column(String(32))
    root_rpid: Mapped[str] = mapped_column(String(32), default="0")
    parent_rpid: Mapped[str] = mapped_column(String(32), default="0")
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey("platform_users.id"))
    author_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("user_snapshots.id"))
    content: Mapped[str] = mapped_column(Text, default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class CommentVersion(Entity, Base):
    __tablename__ = "comment_versions"
    comment_id: Mapped[str] = mapped_column(ForeignKey("comments.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36))
    content: Mapped[str] = mapped_column(Text, default="")
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    author_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("user_snapshots.id"))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class CommentAsset(Entity, Base):
    __tablename__ = "comment_assets"
    __table_args__ = (UniqueConstraint("comment_id", "asset_id", "kind"),)
    comment_id: Mapped[str] = mapped_column(ForeignKey("comments.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    kind: Mapped[str] = mapped_column(String(50), default="image")
    position: Mapped[int] = mapped_column(Integer, default=0)


class CaptureRun(Entity, Base):
    __tablename__ = "capture_runs"
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id"), index=True)
    collection_id: Mapped[str | None] = mapped_column(ForeignKey("collections.id"))
    status: Mapped[str] = mapped_column(String(40), default="running")
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    end_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceAccount(Entity, Base):
    __tablename__ = "source_accounts"
    name: Mapped[str] = mapped_column(String(200))
    secret_encrypted: Mapped[str] = mapped_column(Text)
    uid: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(40), default="unverified")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceSubscription(Entity, Base):
    __tablename__ = "source_subscriptions"
    collection_id: Mapped[str] = mapped_column(ForeignKey("collections.id"), unique=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("source_accounts.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Entity, Base):
    __tablename__ = "jobs"
    kind: Mapped[str] = mapped_column(String(50), index=True)
    target_id: Mapped[str] = mapped_column(String(200), default="")
    account_id: Mapped[str | None] = mapped_column(ForeignKey("source_accounts.id"))
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    dedupe_key: Mapped[str] = mapped_column(String(300), unique=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobAttempt(Entity, Base):
    __tablename__ = "job_attempts"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="running")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(Entity, Base):
    __tablename__ = "outbox_events"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Entity, Base):
    __tablename__ = "audit_logs"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("app_users.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class WatchProgress(Entity, Base):
    __tablename__ = "watch_progress"
    __table_args__ = (UniqueConstraint("user_id", "part_id"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
    part_id: Mapped[str] = mapped_column(ForeignKey("video_parts.id"))
    position: Mapped[float] = mapped_column(Float, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0)


class BackupSet(Entity, Base):
    __tablename__ = "backup_sets"
    status: Mapped[str] = mapped_column(String(40), default="incomplete")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    object_key: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
