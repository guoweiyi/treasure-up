from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
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
    passkey_credential_id: Mapped[str | None] = mapped_column(ForeignKey("passkey_credentials.id", ondelete="SET NULL", use_alter=True, name="fk_sessions_passkey_credential"), index=True)


class LoginAttempt(Entity, Base):
    __tablename__ = "login_attempts"
    client_hash: Mapped[str] = mapped_column(String(64), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)


class PasskeyCredential(Entity, Base):
    __tablename__ = "passkey_credentials"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[str] = mapped_column(Text, unique=True)
    public_key: Mapped[str] = mapped_column(Text)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    name: Mapped[str] = mapped_column(String(100), default="通行密钥")
    transports: Mapped[list] = mapped_column(JSON, default=list)
    aaguid: Mapped[str | None] = mapped_column(String(36))
    device_type: Mapped[str] = mapped_column(String(32), default="single_device")
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasskeyChallenge(Entity, Base):
    __tablename__ = "passkey_challenges"
    challenge: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    binding_hash: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(500))
    rp_id: Mapped[str] = mapped_column(String(253))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasskeyAttempt(Entity, Base):
    __tablename__ = "passkey_attempts"
    client_hash: Mapped[str] = mapped_column(String(64), index=True)
    purpose: Mapped[str] = mapped_column(String(20))


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
    __table_args__ = (Index("ix_videos_created_id", "created_at", "id"),)
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
    source_tags: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    source_availability: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))


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


class VideoStar(Entity, Base):
    __tablename__ = "video_stars"
    __table_args__ = (UniqueConstraint("user_id", "video_id"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)


class PersonalPlaylist(Entity, Base):
    __tablename__ = "personal_playlists"
    __table_args__ = (UniqueConstraint("user_id", "system_key"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000), default="")
    system_key: Mapped[str | None] = mapped_column(String(30))


class PersonalPlaylistItem(Entity, Base):
    __tablename__ = "personal_playlist_items"
    __table_args__ = (UniqueConstraint("playlist_id", "video_id"),
                     Index("ix_personal_playlist_items_order", "playlist_id", "created_at", "id"))
    playlist_id: Mapped[str] = mapped_column(ForeignKey("personal_playlists.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    note: Mapped[str] = mapped_column(String(2000), default="")
    watched: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


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
    monitor_state: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))


class CollectionItem(Entity, Base):
    __tablename__ = "collection_items"
    __table_args__ = (UniqueConstraint("collection_id", "source_resource_id"),)
    collection_id: Mapped[str] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), index=True)
    source_resource_id: Mapped[str] = mapped_column(String(100))
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    source_state: Mapped[str] = mapped_column(String(40), default="available")
    seen_run_id: Mapped[str | None] = mapped_column(String(36))
    observation: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))


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
    __table_args__ = (UniqueConstraint("part_id", "format_key", "kind"),
                     Index("ix_media_variants_kind_created_id", "kind", "created_at", "id"))
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
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))


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
    __table_args__ = (UniqueConstraint("video_id", "rpid"), Index("ix_comment_tree", "video_id", "root_rpid", "posted_at"),
                     Index("ix_comment_pinned_order", "video_id", "is_pinned", "like_count"))
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
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
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
    next_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_video_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    risk_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_pending_encrypted: Mapped[str | None] = mapped_column(Text)
    auto_refresh_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    refresh_phase: Mapped[str] = mapped_column(String(32), default="unsupported", server_default="unsupported")
    refresh_error_code: Mapped[str | None] = mapped_column(String(64))
    refresh_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_refresh_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class SourceAuthorization(Entity, Base):
    __tablename__ = "source_authorizations"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("source_accounts.id", ondelete="CASCADE"))
    account_generation: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    key_encrypted: Mapped[str | None] = mapped_column(Text)
    credential_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))


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
    __table_args__ = (Index("ix_jobs_kind_target_status", "kind", "target_id", "status"),)
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


class VideoStatSnapshot(Entity, Base):
    __tablename__ = "video_stat_snapshots"
    __table_args__ = (UniqueConstraint("video_id", "run_id"), Index("ix_video_stats_observed", "video_id", "observed_at"))
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)


class PlaybackSession(Entity, Base):
    __tablename__ = "playback_sessions"
    user_id: Mapped[str | None] = mapped_column(ForeignKey("app_users.id"), index=True)
    variant_id: Mapped[str] = mapped_column(ForeignKey("media_variants.id"), index=True)
    protocol: Mapped[str] = mapped_column(String(20))
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("storage_profiles.id"))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class StorageObservation(Entity, Base):
    __tablename__ = "storage_observations"
    profile_id: Mapped[str] = mapped_column(ForeignKey("storage_profiles.id"), index=True)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("app_users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(30), default="server_storage")
    latency_ms: Mapped[float] = mapped_column(Float)
    elapsed_ms: Mapped[float] = mapped_column(Float)
    bytes_read: Mapped[int] = mapped_column(Integer)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class IntegrationToken(Entity, Base):
    __tablename__ = "integration_tokens"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("source_accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scope: Mapped[str] = mapped_column(String(40), default="ingest.submit")
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minute_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minute_requests: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    day_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    day_videos: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
