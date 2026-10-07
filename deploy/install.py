"""One-shot Docker installer. Only this command receives the host Docker socket."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from dotenv import dotenv_values
import yaml

from setup import configure_site, create_environment, normalize_bind_address, normalize_origin, show_login

TEMPLATES = Path(__file__).resolve().parent / "install"
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
NAMESPACE = re.compile(r"[a-z0-9][a-z0-9_-]{1,38}")


def atomic_write(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix=".install-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def installation_lock(directory):
    import fcntl  # The installer runs inside the Linux backend image, including on Docker Desktop.
    lock = directory / ".install.lock"
    if lock.is_symlink():
        raise ValueError("The installation lock must not be a symbolic link")
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another installer is using this configuration volume; wait for it to finish") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def read_environment(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Expected a regular .env file, no larger than 64 KiB")
    values = dotenv_values(path, interpolate=False)
    if any(not values.get(key) for key in ("POSTGRES_PASSWORD", "TREASURE_SECRET_KEY", "TREASURE_BACKUP_KEY")):
        raise ValueError("The existing .env is missing a database password, encryption key or backup key; do not generate replacement keys")
    return values


def read_state(path):
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Invalid installation state file")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Invalid installation state file")
    for key in ("project", "namespace", "mode", "completed_version", "attempted_version"):
        if key in state and not isinstance(state[key], str):
            raise ValueError(f"Invalid installation state field: {key}")
    for key in ("completed_version", "attempted_version"):
        if key in state and not VERSION.fullmatch(state[key]):
            raise ValueError(f"Invalid installation version field: {key}")
    if "images" in state and (not isinstance(state["images"], dict) or
                              any(not isinstance(value, str) for value in state["images"].values())):
        raise ValueError("Invalid installation image metadata")
    return state


def install(args):
    directory = Path(args.config_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Mount the persistent configuration volume at /config before running this installer")
    if not Path("/var/run/docker.sock").is_socket():
        raise ValueError("Mount /var/run/docker.sock for this one-time Docker management command")
    release = json.loads((TEMPLATES / "release.json").read_text(encoding="utf-8"))
    version = release.get("version", "") if isinstance(release, dict) else ""
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("The installer image contains invalid release metadata")
    environment_file, state_file = directory / ".env", directory / "installation.json"
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("TREASURE_", "POSTGRES_", "COMPOSE_")) and key != "DOCKER_CONTEXT"}
    environment["DOCKER_HOST"] = "unix:///var/run/docker.sock"

    def docker(*command, capture=False, timeout=900):
        result = subprocess.run(["docker", *command], env=environment, cwd=directory, check=False,
                                capture_output=capture, text=True, timeout=timeout)
        if result.returncode:
            # Captured Docker metadata can include configuration. Do not echo it on error.
            raise ValueError(f"Docker {command[0]} failed (exit {result.returncode}); existing configuration and data were preserved")
        return result.stdout.strip() if capture else ""

    with installation_lock(directory):
        state = read_state(state_file)
        project = args.project_name or state.get("project") or "treasure-up"
        namespace = args.namespace or state.get("namespace") or os.getenv("TREASURE_INSTALL_NAMESPACE") or "yunyunjuan"
        mode = args.mode or state.get("mode") or "light"
        if not PROJECT.fullmatch(project) or not NAMESPACE.fullmatch(namespace) or mode not in {"light", "full"}:
            raise ValueError("Invalid project name, Docker Hub namespace or installation mode")
        if state.get("project") and state["project"] != project:
            raise ValueError("This configuration volume already belongs to another project; use its original project name")
        previous = [state[key] for key in ("completed_version", "attempted_version") if state.get(key)]
        if previous and tuple(map(int, version.split("."))) < max(tuple(map(int, value.split("."))) for value in previous):
            raise ValueError("This command would downgrade an existing installation; restore a matching backup instead")
        if args.show_login:
            show_login(environment_file)
            return
        if args.port is not None and not 1 <= args.port <= 65535:
            raise ValueError("Published port must be between 1 and 65535")
        if args.origin is not None:
            normalize_origin(args.origin, allow_http=args.allow_http)
        if args.bind_address is not None:
            normalize_bind_address(args.bind_address)
        docker("version", "--format", "{{.Server.Version}}", capture=True, timeout=30)
        docker("compose", "version", "--short", capture=True, timeout=30)
        if args.import_env:
            if environment_file.exists():
                raise ValueError("Configuration already exists; remove --import-env and reuse the saved configuration")
            imported = Path(args.import_env)
            read_environment(imported)
            atomic_write(environment_file, imported.read_text(encoding="utf-8-sig"))
            print("Imported the existing configuration; database passwords and encryption keys were preserved.")
        if not environment_file.exists():
            volumes = set(docker("volume", "ls", "--format", "{{.Name}}", capture=True, timeout=30).splitlines())
            owned = {f"{project}_{name}" for name in ("database", "media", "scratch", "queue", "backup")}
            if volumes & owned:
                raise ValueError("Existing project data was found without its .env. Mount the original .env read-only and use --import-env /previous/.env; never replace the original encryption keys")
            create_environment(environment_file, origin=args.origin or f"http://localhost:{args.port or 8788}",
                               port=args.port or 8788, bind_address=args.bind_address or "127.0.0.1", allow_http=args.allow_http)
        else:
            read_environment(environment_file)
            configure_site(environment_file, origin=args.origin, bind_address=args.bind_address,
                           port=args.port, allow_http=args.allow_http)
        settings = read_environment(environment_file)
        images = {
            "backend": args.backend_image or f"docker.io/{namespace}/treasure-up-backend:{version}",
            "web": args.web_image or f"docker.io/{namespace}/treasure-up-web:{version}",
        }
        if any(not reference or re.search(r"[\s\x00-\x1f]", reference) for reference in images.values()):
            raise ValueError("Invalid image reference")
        config = yaml.safe_load((TEMPLATES / "compose.yaml").read_text(encoding="utf-8"))
        config = {key: value for key, value in config.items() if not key.startswith("x-")}
        config["name"] = project
        for service in config["services"].values():
            service.pop("build", None)
            for component in images:
                if f"treasure-up-{component}:" in service.get("image", ""):
                    service["image"] = images[component]
        atomic_write(directory / "compose.yaml", yaml.safe_dump(config, sort_keys=False))
        atomic_write(directory / "compose.light.yaml", (TEMPLATES / "compose.light.yaml").read_text(encoding="utf-8"))
        state.update(project=project, namespace=namespace, mode=mode)
        atomic_write(state_file, json.dumps(state, indent=2) + "\n")
        base = ["compose", "--project-name", project, "--env-file", str(environment_file),
                "-f", str(directory / "compose.yaml")]
        light = [*base, "-f", str(directory / "compose.light.yaml")]
        selected = light if mode == "light" else base
        print(f"Installing Treasure Up {version} ({mode}) in Docker project {project}.", flush=True)
        if not args.no_pull:
            print("Downloading the selected version before changing running services.", flush=True)
            docker(*selected, "pull", "--policy", "always")
        # A failed readiness check may happen after database migration. Record
        # the attempted version durably before init can run, not only on success.
        state["attempted_version"] = version
        atomic_write(state_file, json.dumps(state, indent=2) + "\n")
        docker(*selected, "up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180")
        # Retire the previous mode only after replacement services are healthy.
        retired = ["download-worker", "media-worker"] if mode == "light" else ["worker"]
        docker(*light, "--profile", "full-workers", "stop", *retired)
        state.update(completed_version=version, images=images)
        atomic_write(state_file, json.dumps(state, indent=2) + "\n")
        print(f"Ready: {settings.get('TREASURE_PASSKEY_ORIGIN', 'http://localhost:8788')}")
        print("Configuration and keys are kept in the configuration volume. Repeat this Docker command to update.")
        print("Use --show-login in a private interactive terminal to view the saved initial login; change it after signing in.")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default="/config")
    parser.add_argument("--project-name")
    parser.add_argument("--namespace", help="Docker Hub username/organization; default yunyunjuan")
    parser.add_argument("--origin", help="Public HTTPS origin, or http://localhost / a LAN IPv4 address")
    parser.add_argument("--bind-address", help="127.0.0.1 by default; 0.0.0.0 exposes the web port to LAN/proxy clients")
    parser.add_argument("--port", type=int, help="Published web port; default 8788 on first install")
    parser.add_argument("--mode", choices=("light", "full"), help="Defaults to light on first install; later commands preserve the selected mode")
    parser.add_argument("--allow-http", action="store_true", help="Explicitly permit a public HTTP origin without passkeys")
    parser.add_argument("--import-env", type=Path, help="One-time migration of the original .env, mounted read-only")
    parser.add_argument("--show-login", action="store_true")
    parser.add_argument("--backend-image", help="Explicit image override for local verification or an offline mirror")
    parser.add_argument("--web-image", help="Explicit image override for local verification or an offline mirror")
    parser.add_argument("--no-pull", action="store_true", help="Use only existing local images, including PostgreSQL and Redis")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        install(args)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Installation did not complete: {error}\n")


if __name__ == "__main__":
    main()
