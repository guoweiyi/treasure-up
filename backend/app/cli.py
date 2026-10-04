import argparse
import getpass
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Setting, StorageProfile, User
from app.schemas import BackupSettings, DisplaySettings, IngestPolicy, PlaybackSettings, StatisticsSettings
from app.security import hash_password


def initialize():
    if settings.database_url.startswith("sqlite") and not settings.dev_sqlite:
        raise RuntimeError("生产请配置PostgreSQL；SQLite仅用于显式开发验证")
    Fernet(settings.encryption_key().encode())
    if not settings.admin_password:
        raise RuntimeError("首次初始化需要TREASURE_ADMIN_PASSWORD，请在本地部署环境中设置")
    password_hash = hash_password(settings.admin_password)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    with SessionLocal() as db:
        from app.storage.service import lock_storage_configuration
        lock_storage_configuration(db)
        if not db.scalar(select(User).where(User.username == settings.admin_username)):
            db.add(User(username=settings.admin_username, password_hash=password_hash, role="admin"))
        if not db.scalar(select(StorageProfile.id).limit(1)):
            db.add(StorageProfile(name="本地媒体库", kind="local", config={"root": str(settings.media_root.resolve())}, is_default=True))
        for key, value in {"display": DisplaySettings().model_dump(), "ingest": IngestPolicy().model_dump(), "backup": BackupSettings().model_dump(),
                           "playback": PlaybackSettings().model_dump(), "statistics": StatisticsSettings().model_dump()}.items():
            if not db.get(Setting, key):
                db.add(Setting(key=key, value=value))
        db.commit()
    print("数据库迁移和初始管理员已就绪；已有账号密码不会被覆盖。")


def main():
    parser = argparse.ArgumentParser(prog="treasure", description="Treasure Up maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    execute = commands.add_parser("run-job", help="显式执行一个已登记任务；开发验证用")
    execute.add_argument("job_id")
    local_import = commands.add_parser("import-local", help="导入已有本地媒体，不访问源站")
    local_import.add_argument("path", type=Path)
    local_import.add_argument("--metadata", type=Path)
    local_import.add_argument("--danmaku", type=Path)
    password = commands.add_parser("reset-password")
    password.add_argument("username")
    backup = commands.add_parser("backup")
    backup.add_argument("--destination")
    for name in ["verify-backup", "restore-backup"]:
        sub = commands.add_parser(name)
        sub.add_argument("destination", type=Path)
        sub.add_argument("backup_id")
        if name == "restore-backup":
            sub.add_argument("--database-url", required=True)
            sub.add_argument("--media-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init":
        initialize()
    elif args.command == "run-job":
        from app.jobs import run_job_id
        print(json.dumps(run_job_id(args.job_id), ensure_ascii=False, default=str))
    elif args.command == "import-local":
        from app.ingest.local_import import import_local
        metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else {}
        with SessionLocal() as db:
            video = import_local(db, args.path, metadata, args.danmaku)
            db.commit()
            print(json.dumps({"video_id": video.id, "bvid": video.bvid, "status": video.capture_status}, ensure_ascii=False))
    elif args.command == "reset-password":
        value = getpass.getpass("新密码（至少12位）：")
        if value != getpass.getpass("再次输入："):
            raise RuntimeError("两次密码不同")
        from sqlalchemy import delete
        from app.models import UserSession
        with SessionLocal() as db:
            # Serialize password/session changes while permitting a concurrent
            # passkey revocation's audit FK check on this user.
            user = db.scalar(select(User).where(User.username == args.username).with_for_update(key_share=True))
            if not user:
                raise RuntimeError("账号不存在")
            user.password_hash = hash_password(value)
            db.execute(delete(UserSession).where(UserSession.user_id == user.id))
            db.commit()
        print("密码已更新，旧会话已撤销")
    elif args.command == "backup":
        from app.backup import create_backup
        with SessionLocal() as db:
            item = create_backup(db, destination=args.destination)
            print(json.dumps({"id": item.id, "status": item.status, "error": item.error}, ensure_ascii=False))
    elif args.command == "verify-backup":
        from app.backup import verify_backup
        print(json.dumps(verify_backup(args.destination, args.backup_id), default=str))
    elif args.command == "restore-backup":
        from app.backup import restore_backup
        print(json.dumps(restore_backup(args.destination, args.backup_id, args.database_url, args.media_root), default=str))


if __name__ == "__main__":
    main()
