"""Initialize Compose configuration volumes without Docker access or network calls."""
import argparse
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from urllib.parse import quote

from dotenv import dotenv_values

from setup import configure_site, create_environment, normalize_origin, show_login

CONFIG = Path("/config")
EXPORTS = Path("/exports")
DATABASE = Path("/existing-database")
RELEASE = Path(__file__).resolve().parent / "release.json"
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def read_private(path):
    if path.is_symlink():
        raise ValueError("Configuration files must not be symbolic links")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536:
            raise ValueError("Expected a regular configuration file no larger than 64 KiB")
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError("Configuration file exceeds 64 KiB")
    return content.decode("utf-8-sig")


def read_environment(path):
    text = read_private(path)
    keys = re.findall(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=", text, re.MULTILINE)
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate settings in the original .env must be resolved before importing")
    values = dotenv_values(stream=io.StringIO(text), interpolate=False)
    required = ("POSTGRES_PASSWORD", "TREASURE_SECRET_KEY", "TREASURE_BACKUP_KEY")
    if any(not isinstance(values.get(key), str) or not values[key] for key in required):
        raise ValueError("The original .env is missing its database password, encryption key or backup key; never replace the original keys")
    if any(value is not None and any(char in value for char in ("\x00", "\r", "\n")) for value in values.values()):
        raise ValueError("Configuration values must use single lines without NUL characters")
    return values, text


def atomic_write(path, content, *, uid=0, mode=0o600):
    if path.is_symlink():
        raise ValueError("Configuration output must not be a symbolic link")
    descriptor, temporary = tempfile.mkstemp(prefix=".setup-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fchown(stream.fileno(), uid, uid)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def configuration_lock():
    import fcntl
    descriptor = os.open(CONFIG / ".install.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Invalid configuration lock")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another setup command is using this configuration volume; wait for it to finish") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def version_state(path):
    try:
        release = json.loads(read_private(RELEASE))
        state = json.loads(read_private(path)) if path.exists() or path.is_symlink() else {}
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in release or saved installation metadata") from None
    version = release.get("version") if isinstance(release, dict) else None
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("The setup image contains invalid release metadata")
    if not isinstance(state, dict):
        raise ValueError("Invalid saved installation metadata")
    for key in ("attempted_version", "completed_version"):
        previous = state.get(key)
        if previous is not None:
            if not isinstance(previous, str) or not VERSION.fullmatch(previous):
                raise ValueError("Invalid saved installation version")
            if tuple(map(int, version.split("."))) < tuple(map(int, previous.split("."))):
                raise ValueError("This setup image would downgrade an existing installation; restore a matching backup instead")
    return version, state


def initialize(args):
    for directory in (CONFIG, EXPORTS, DATABASE):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Mount the configuration, export and read-only database volumes before running setup")
    master, state_path = CONFIG / ".env", CONFIG / "installation.json"
    with configuration_lock():
        if args.show_login:
            if args.import_env:
                raise ValueError("--show-login is separate from configuration changes")
            show_login(master)
            return
        version, state = version_state(state_path)
        if args.origin:
            normalize_origin(args.origin, allow_http=args.allow_http)
        if args.import_env:
            if master.exists() or master.is_symlink():
                raise ValueError("Configuration already exists; remove --import-env to reuse its saved keys")
            _values, original = read_environment(args.import_env)
            atomic_write(master, original)
        if not master.exists() and not master.is_symlink():
            if next(DATABASE.iterdir(), None) is not None:
                raise ValueError("Existing database data was found without its .env; mount the original file read-only and use --import-env /previous/.env. Never generate replacement keys")
            # Even an interactive setup does not print secrets to container logs.
            with tempfile.TemporaryDirectory(prefix=".new-", dir=CONFIG) as staging, io.StringIO() as quiet:
                initial = Path(staging) / ".env"
                create_environment(initial, origin=args.origin or "http://localhost:8788",
                                   allow_http=args.allow_http, stream=quiet)
                atomic_write(master, initial.read_text(encoding="utf-8"))
        else:
            read_environment(master)
            configure_site(master, origin=args.origin, allow_http=args.allow_http)
        values, _original = read_environment(master)
        os.chown(master, 0, 0)
        os.chmod(master, 0o600)
        runtime = {
            "TREASURE_DATABASE_URL": "postgresql+psycopg://treasure:" + quote(values["POSTGRES_PASSWORD"], safe="") + "@postgres:5432/treasure",
            "TREASURE_SECRET_KEY": values["TREASURE_SECRET_KEY"],
        }
        defaults = {
            "TREASURE_COOKIE_SECURE": "true", "TREASURE_ALLOWED_HOSTS": "localhost,127.0.0.1",
            "TREASURE_PASSKEY_RP_ID": "localhost", "TREASURE_PASSKEY_ORIGIN": "http://localhost:8788",
            "TREASURE_PASSKEY_RP_NAME": "Treasure Up", "TREASURE_PASSKEYS_ENABLED": "true",
        }
        runtime.update({key: values.get(key) or default for key, default in defaults.items()})
        runtime["TREASURE_TRUSTED_ORIGINS"] = values.get("TREASURE_TRUSTED_ORIGINS") or ""
        exports = {
            "runtime": runtime,
            "init": {"TREASURE_ADMIN_USERNAME": values.get("TREASURE_ADMIN_USERNAME") or "admin",
                     "TREASURE_ADMIN_PASSWORD": values.get("TREASURE_ADMIN_PASSWORD") or ""},
            "backup": {"TREASURE_BACKUP_KEY": values["TREASURE_BACKUP_KEY"]},
        }
        # Record the version before Compose can start a database migration, even
        # if that later migration or service readiness fails.
        state.update(attempted_version=version, config_format=1)
        atomic_write(state_path, json.dumps(state, indent=2) + "\n")
        for role in (*exports, "postgres"):
            directory = EXPORTS / role
            if directory.is_symlink():
                raise ValueError("Export directories must not be symbolic links")
            directory.mkdir(exist_ok=True)
            os.chown(directory, 0, 0)
            os.chmod(directory, 0o755)
            if role == "postgres":
                atomic_write(directory / "password", values["POSTGRES_PASSWORD"], uid=999, mode=0o400)
            else:
                filename = "runtime.json" if role == "runtime" else "role.json"
                atomic_write(directory / filename, json.dumps(exports[role]) + "\n", uid=10001, mode=0o400)
        print("Configuration is ready. Saved passwords and encryption keys were preserved.")
        print("Use the setup service with --show-login in a private interactive terminal to view the initial login.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default=os.environ.get("TREASURE_PUBLIC_ORIGIN") or None,
                        help="Public HTTPS origin, or http://localhost / a LAN IPv4 address")
    parser.add_argument("--allow-http", action="store_true", help="Explicitly allow a public HTTP origin without passkeys")
    parser.add_argument("--import-env", type=Path, help="Original .env mounted read-only, for migration only")
    parser.add_argument("--show-login", action="store_true", help="Show the saved initial login only in an interactive terminal")
    args = parser.parse_args()
    try:
        initialize(args)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Configuration setup did not complete: {error}\n")


if __name__ == "__main__":
    main()
