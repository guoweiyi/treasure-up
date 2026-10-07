"""Run the installer with its persistent configuration volume managed automatically."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from uuid import uuid4

from install import NAMESPACE, PROJECT, TEMPLATES, VERSION, build_parser, read_environment

INSTALLER_LABEL = "com.treasure-up.installer.project"


class Interrupted(Exception):
    def __init__(self, number):
        self.number = number


def launch(args):
    if args.config_dir != "/config":
        raise ValueError("Use --config-volume to choose the persistent configuration volume")
    if not Path("/var/run/docker.sock").is_socket():
        raise ValueError("Mount /var/run/docker.sock for this one-time Docker management command")
    project = args.project_name or "treasure-up"
    namespace = args.namespace or os.getenv("TREASURE_INSTALL_NAMESPACE") or "yunyunjuan"
    volume = args.config_volume or f"{project}-config"
    if not PROJECT.fullmatch(project) or not NAMESPACE.fullmatch(namespace):
        raise ValueError("Invalid project name or Docker Hub namespace")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", volume):
        raise ValueError("Invalid configuration volume name")
    release = json.loads((TEMPLATES / "release.json").read_text(encoding="utf-8"))
    version = release.get("version", "") if isinstance(release, dict) else ""
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("The installer image contains invalid release metadata")
    image = args.installer_image or f"docker.io/{namespace}/treasure-up:{version}"
    if image.startswith("-") or re.search(r"[\s\x00-\x1f]", image):
        raise ValueError("Invalid installer image reference")
    if args.import_env:
        read_environment(args.import_env)
    child_args = []
    for name in ("project_name", "namespace", "origin", "bind_address", "port", "mode", "backend_image", "web_image"):
        value = getattr(args, name)
        if value is not None:
            child_args.extend(["--" + name.replace("_", "-"), str(value)])
    for name in ("allow_http", "show_login", "no_pull"):
        if getattr(args, name):
            child_args.append("--" + name.replace("_", "-"))
    if args.import_env:
        child_args.extend(["--import-env", "/run/import.env"])
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("TREASURE_", "POSTGRES_", "COMPOSE_")) and key != "DOCKER_CONTEXT"}
    environment["DOCKER_HOST"] = "unix:///var/run/docker.sock"

    def docker(*command, capture=False, timeout=60):
        return subprocess.run(["docker", *command], env=environment, check=False, text=True,
                              stdout=subprocess.PIPE if capture else None, timeout=timeout)

    name = f"{project}-installer-{uuid4().hex[:12]}"
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    create = ["create", "--name", name, "--pull", "never" if args.no_pull else "missing",
              "--label", f"{INSTALLER_LABEL}={project}",
              "--label", "com.treasure-up.installer.role=worker", "--network", "none", "--log-driver", "none",
              "--mount", "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock",
              "--mount", f"type=volume,source={volume},target=/config", "--entrypoint", "python"]
    if interactive:
        create.extend(["--interactive", "--tty"])
    create.extend([image, "/app/install.py", *child_args])

    def interrupt(number, _frame):
        raise Interrupted(number)

    handlers = {number: signal.signal(number, interrupt) for number in (signal.SIGINT, signal.SIGTERM)}
    created = False
    try:
        if docker(*create, capture=True, timeout=900).returncode:
            raise ValueError("Could not create the installation container")
        created = True
        if args.import_env:
            # Paths on the outer container are not paths on the Docker host.
            # Copy the validated file privately, without publishing credentials in argv or env.
            if docker("cp", str(args.import_env.resolve()), name + ":/run/import.env").returncode:
                raise ValueError("Could not copy the original configuration into the installer")
        docker("start", "--attach", *(["--interactive"] if interactive else []), name, timeout=1800)
        result = docker("inspect", "--format", "{{json .State}}", name, capture=True)
        if result.returncode:
            raise ValueError("Could not verify the installation container's exit status")
        state = json.loads(result.stdout)
        code = state.get("ExitCode")
        if state.get("Status") != "exited" or not isinstance(code, int) or not 0 <= code <= 255:
            raise ValueError("The installation container did not finish normally")
        return code
    finally:
        # Only the temporary installer is removed. Services, configuration and data remain.
        for number in handlers:
            signal.signal(number, signal.SIG_IGN)
        try:
            removed = docker("rm", "--force", name, capture=True)
            if created and removed.returncode:
                raise ValueError(f"Could not remove the temporary installer {name}; remove it after restoring Docker access")
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)


def main():
    parser = build_parser()
    parser.description = __doc__
    parser.add_argument("--config-volume", help="Persistent Docker volume; defaults to <project>-config")
    parser.add_argument("--installer-image", help="Explicit installer image override for local verification")
    args = parser.parse_args()
    try:
        return launch(args)
    except Interrupted as error:
        return 128 + error.number
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Installation did not complete: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
