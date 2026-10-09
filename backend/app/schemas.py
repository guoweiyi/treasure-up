from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class StarInput(Input):
    starred: bool


class UserCreate(Input):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "editor", "reader"] = "reader"


class UserUpdate(Input):
    role: Literal["admin", "editor", "reader"] | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


class Annotation(Input):
    title_override: str | None = Field(default=None, max_length=2000)
    description_override: str | None = Field(default=None, max_length=50000)
    notes: str = Field(default="", max_length=50000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    starred: bool = False

    @field_validator("tags")
    @classmethod
    def tags_valid(cls, values):
        if any(len(v) > 100 for v in values):
            raise ValueError("标签最长100字")
        return list(dict.fromkeys(v.strip() for v in values if v.strip()))


class CreatorAnnotation(Input):
    alias: str | None = Field(default=None, max_length=500)
    description_override: str | None = Field(default=None, max_length=50000)
    notes: str = Field(default="", max_length=50000)
    tags: list[str] = Field(default_factory=list, max_length=50)


class AccountInput(Input):
    name: str = Field(min_length=1, max_length=200)
    cookie: str = Field(min_length=1, max_length=65536)
    refresh_token: str | None = Field(default=None, max_length=4096)
    auto_refresh_enabled: bool = True


class AccountUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    cookie: str | None = Field(default=None, min_length=1, max_length=65536)
    refresh_token: str | None = Field(default=None, max_length=4096)
    auto_refresh_enabled: bool | None = None


class SourceResolve(Input):
    value: str = Field(min_length=1, max_length=2000)
    kind: Literal["favorite", "creator"] = "favorite"


class SourceScan(Input):
    full: bool = False


class IngestPolicy(Input):
    initial_strategy: Literal["all", "latest", "new_only"] = "all"
    initial_limit: int = Field(default=100, ge=1, le=10000)
    incremental_pages: int = Field(default=3, ge=1, le=100)
    full_scan_interval_hours: int = Field(default=24, ge=1, le=720)
    force_full_scan: bool = False
    include_paid_videos: bool = False
    quality: Literal["best", "4320p", "8k", "2160p", "4k", "1440p", "1080p", "720p", "480p", "360p"] = "best"
    create_compatible_copy: bool = True
    prefer_h264: bool = False
    prefer_dolby_vision: bool = True
    prefer_dolby_atmos: bool = True
    request_interval_seconds: float = Field(default=8, ge=1, le=120)
    asset_interval_seconds: float = Field(default=0.1, ge=0.05, le=5)
    video_interval_seconds: float = Field(default=180, ge=10, le=3600)
    interval_jitter_seconds: float = Field(default=10, ge=0, le=300)
    risk_cooldown_seconds: int = Field(default=1800, ge=60, le=86400)
    download_rate_bytes: int | None = Field(default=None, ge=10000, le=1_000_000_000)
    fragment_concurrency: int = Field(default=1, ge=1, le=3)
    refresh_danmaku: bool = False
    request_budget: int = Field(default=100, ge=1, le=10000)
    download_media: bool = True
    fetch_comments: bool = True
    comment_top_limit: int = Field(default=500, ge=0, le=10000)
    comment_scan_limit: int = Field(default=2000, ge=1, le=100000)
    comment_reply_total_limit: int = Field(default=200, ge=0, le=100000)
    comment_reply_per_root_limit: int = Field(default=10, ge=0, le=1000)
    comment_asset_count_limit: int = Field(default=300, ge=0, le=10000)
    comment_asset_bytes_limit: int = Field(default=25 * 1024**2, ge=0, le=1024**3)
    fetch_danmaku: bool = True
    fetch_subtitles: bool = True
    include_auto_subtitles: bool = True
    max_download_bytes: int = Field(default=30_000_000_000, ge=1_000_000, le=500_000_000_000)
    max_pages: int = Field(default=100, ge=1, le=10000)

    @field_validator("request_interval_seconds", "video_interval_seconds", "risk_cooldown_seconds")
    @classmethod
    def conservative_intervals(cls, value, info):
        # Accept legacy saved policies, but never resume their previous bursts.
        floors = {"request_interval_seconds": 8, "video_interval_seconds": 180,
                  "risk_cooldown_seconds": 1800}
        return max(value, floors[info.field_name])

    @field_validator("fragment_concurrency")
    @classmethod
    def serial_fragments(cls, value):
        # Keep old values parseable while removing parallel downloads.
        return 1


class SourceInput(Input):
    kind: Literal["favorite", "creator"] = "favorite"
    source_id: str = Field(pattern=r"^\d{1,32}$")
    title: str = Field(min_length=1, max_length=500)
    account_id: str
    enabled: bool = False
    interval_minutes: int = Field(default=1440, ge=15, le=525600)
    policy: IngestPolicy = Field(default_factory=IngestPolicy)


class SourceUpdate(Input):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    account_id: str | None = None
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=525600)
    policy: IngestPolicy | None = None


class JobInput(Input):
    kind: Literal["scan_collection", "archive_video", "refresh_comments", "refresh_stats", "verify_account"]
    target_id: str = Field(min_length=1, max_length=200)
    account_id: str | None = None
    policy: IngestPolicy = Field(default_factory=IngestPolicy)


class StorageInput(Input):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["local", "s3", "oss", "cos", "obs", "upyun", "qiniu", "minio", "rain", "spaces", "r2", "oracle", "b2", "onedrive", "onedrive_cn", "sharepoint", "sharepoint_cn"]
    config: dict = Field(default_factory=dict)
    credentials: dict | None = None
    is_default: bool = False
    enabled: bool = True


class StorageDiscoveryInput(Input):
    kind: Literal["local", "s3", "oss", "cos", "obs", "upyun", "qiniu", "minio", "rain", "spaces", "r2", "oracle", "b2", "onedrive", "onedrive_cn", "sharepoint", "sharepoint_cn"]
    config: dict = Field(default_factory=dict)
    credentials: dict | None = None
    profile_id: str | None = Field(default=None, max_length=100)


class MigrationInput(Input):
    target_profile_id: str
    asset_ids: list[str] | None = Field(default=None, max_length=10000)


class PlaybackInput(Input):
    part_id: str
    variant_id: str | None = None
    route_id: str | None = None
    protocol: Literal["auto", "hls", "file"] = "auto"


class PlaybackObservationInput(Input):
    route_id: str
    latency_ms: float = Field(ge=0, le=120000, allow_inf_nan=False)
    elapsed_ms: float = Field(gt=0, le=120000, allow_inf_nan=False)
    bytes_read: int = Field(ge=0, le=131072)
    succeeded: bool


class VideoSyncInput(Input):
    target_profile_id: str


class ReplicaSyncSelection(Input):
    video_id: str = Field(min_length=1, max_length=36)
    # Omitted means every version. An explicitly empty selection is an error.
    variant_ids: list[str] | None = Field(default=None, min_length=1, max_length=200)


class ReplicaBatchSyncInput(VideoSyncInput):
    items: list[ReplicaSyncSelection] = Field(min_length=1, max_length=50)


class PurgeLocationInput(Input):
    confirm: Literal["DELETE"]


class PlaybackSettings(Input):
    package_long_videos: bool = False
    min_duration_seconds: int = Field(default=300, ge=0, le=86400)
    min_size_mb: int = Field(default=64, ge=32, le=4096)
    segment_seconds: int = Field(default=6, ge=2, le=20)
    analyze_loudness: bool = False
    probe_bytes: int = Field(default=65536, ge=16384, le=131072)


class MediaPreparationInput(Input):
    package: bool = True
    analyze_loudness: bool = False


class ProgressInput(Input):
    position: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)
    duration: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)


class DisplaySettings(Input):
    site_name: str = Field(default="Treasure Up", max_length=100)
    default_danmaku: bool = True
    allow_guest_access: bool = False


class BackupSettings(Input):
    destination: str = Field(default="", max_length=2000)
    key_id: str = Field(default="", max_length=100)
    interval_hours: int = Field(default=12, ge=1, le=720)
    retention_days: int = Field(default=30, ge=1, le=3650)
    enabled: bool = False


class StatisticsSettings(Input):
    enabled: bool = True
    account_id: str | None = None
    interval_hours: int = Field(default=6, ge=1, le=720)
    refresh_danmaku: bool = False
    refresh_comments: bool = True


class SettingsInput(Input):
    display: DisplaySettings | None = None
    ingest: IngestPolicy | None = None
    backup: BackupSettings | None = None
    statistics: StatisticsSettings | None = None
    playback: PlaybackSettings | None = None
