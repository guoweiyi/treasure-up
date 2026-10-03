from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TREASURE_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://treasure@postgres/treasure"
    redis_url: str = "redis://redis:6379/0"
    data_dir: Path = Path("runtime")
    media_root: Path = Path("runtime/media")
    scratch_dir: Path = Path("runtime/scratch")
    secret_key: str = ""
    secret_key_file: Path | None = None
    cookie_secure: bool = True
    session_hours: int = 72
    admin_username: str = "admin"
    admin_password: str = ""
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    yt_dlp_path: str = "yt-dlp"
    source_request_interval: float = 1.5
    x_accel_prefix: str = ""
    dev_sqlite: bool = False
    allowed_hosts: str = "localhost,127.0.0.1"
    lease_seconds: int = 180
    login_limit: int = 10
    login_window_seconds: int = 300
    passkeys_enabled: bool = True
    passkey_rp_id: str = "localhost"
    passkey_origin: str = "http://localhost:8788"
    passkey_rp_name: str = "Treasure Up"
    passkey_challenge_seconds: int = 300
    passkey_limit: int = 10
    passkey_window_seconds: int = 300

    def encryption_key(self) -> str:
        if self.secret_key_file:
            return self.secret_key_file.read_text(encoding="utf-8").strip()
        return self.secret_key


settings = Settings()
