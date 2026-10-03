from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


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


class IngestPolicy(Input):
    quality: Literal["best", "4320p", "8k", "2160p", "4k", "1440p", "1080p", "720p", "480p", "360p"] = "best"
    create_compatible_copy: bool = True
    prefer_h264: bool = False
    request_budget: int = Field(default=100, ge=1, le=10000)
    download_media: bool = True
    fetch_comments: bool = True
    fetch_danmaku: bool = True
    fetch_subtitles: bool = True
    include_auto_subtitles: bool = True
    max_download_bytes: int = Field(default=30_000_000_000, ge=1_000_000, le=500_000_000_000)
    max_pages: int = Field(default=100, ge=1, le=10000)


class SourceInput(Input):
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
    kind: Literal["scan_collection", "archive_video", "refresh_comments", "verify_account"]
    target_id: str = Field(min_length=1, max_length=200)
    account_id: str | None = None
    policy: IngestPolicy = Field(default_factory=IngestPolicy)


class StorageInput(Input):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["local", "s3", "oss"]
    config: dict = Field(default_factory=dict)
    credentials: dict | None = None
    is_default: bool = False
    enabled: bool = True


class MigrationInput(Input):
    target_profile_id: str
    asset_ids: list[str] | None = Field(default=None, max_length=10000)


class PlaybackInput(Input):
    part_id: str
    variant_id: str | None = None


class ProgressInput(Input):
    position: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)
    duration: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)


class DisplaySettings(Input):
    site_name: str = Field(default="Treasure Up", max_length=100)
    default_danmaku: bool = True


class BackupSettings(Input):
    destination: str = Field(default="", max_length=2000)
    key_id: str = Field(default="", max_length=100)
    interval_hours: int = Field(default=12, ge=1, le=720)
    retention_days: int = Field(default=30, ge=1, le=3650)
    enabled: bool = False


class SettingsInput(Input):
    display: DisplaySettings | None = None
    ingest: IngestPolicy | None = None
    backup: BackupSettings | None = None
