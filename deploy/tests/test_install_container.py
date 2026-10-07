"""Opt-in source-free installation check using already-built local Docker images.

The test runner uses Python, but the simulated user installation only runs Docker.
Every container and volume belongs to a new random Compose project and is removed
in finally; the application's normal project and data are never touched.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[1]
ROOT = DEPLOY.parent
sys.path.insert(0, str(DEPLOY))
from release import deployment_content
from check_service import run_check

pytestmark = pytest.mark.skipif(
    os.environ.get("TREASURE_RUN_INSTALL_TESTS") != "1",
    reason="Set TREASURE_RUN_INSTALL_TESTS=1 after building local backend/web images",
)


def _redact(output, environment_file):
    if environment_file.is_file():
        for line in environment_file.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and value and key.endswith(("_PASSWORD", "_KEY")):
                output = output.replace(value, "[redacted]")
    return output


def test_fresh_install_uses_only_containers_and_preserves_initialization(tmp_path):
    version = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))["version"]
    images = {"images": {component: {"reference": os.environ.get(
        f"TREASURE_INSTALL_{component.upper()}_IMAGE", f"treasure-up-{component}:{version}"
    )} for component in ("backend", "web")}}
    for name in ("compose.yaml", "compose.light.yaml", "compose.setup.yaml"):
        source = (ROOT / name).read_text(encoding="utf-8")
        (tmp_path / name).write_text(deployment_content(name, source, images), encoding="utf-8")
    config = yaml.safe_load((tmp_path / "compose.yaml").read_text(encoding="utf-8"))
    assert all(not service.get("build") for service in config["services"].values())
    # Refuse a future template change that could attach existing named/external volumes.
    assert all(not value or (not value.get("name") and not value.get("external"))
               for value in config.get("volumes", {}).values())
    assert {path.name for path in tmp_path.iterdir()} == {"compose.yaml", "compose.light.yaml", "compose.setup.yaml"}

    project = "treasure-install-test-" + uuid4().hex[:16]
    environment_file = tmp_path / ".env"
    empty_file = tmp_path / "empty.env"
    empty_file.write_text("")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("TREASURE_", "POSTGRES_", "COMPOSE_"))}

    def command(arguments, *, timeout=240, check=True):
        result = subprocess.run(["docker", *arguments], cwd=tmp_path, env=environment,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if check and result.returncode:
            raise AssertionError("Installation Docker command failed:\n" +
                                 _redact(result.stdout + result.stderr, environment_file)[-8000:])
        return result

    assert not command(["ps", "--all", "--quiet", "--filter", f"label=com.docker.compose.project={project}"]).stdout.strip()
    setup = ["compose", "--project-name", project, "--env-file", str(empty_file),
             "-f", str(tmp_path / "compose.setup.yaml"), "run", "--rm", "-T", "--pull", "never"]
    if hasattr(os, "getuid"):
        setup += ["--user", f"{os.getuid()}:{os.getgid()}"]
    setup += ["setup"]
    stack = ["compose", "--project-name", project, "--env-file", str(environment_file),
             "-f", str(tmp_path / "compose.yaml"), "-f", str(tmp_path / "compose.light.yaml")]

    # Reserve the loopback port until setup has generated its matching public origin.
    reservation = socket.socket()
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
    started = False
    try:
        first = command(setup + ["--port", str(port)], timeout=90)
        settings = dict(line.split("=", 1) for line in environment_file.read_text(encoding="utf-8").splitlines())
        secrets = [settings[key] for key in ("POSTGRES_PASSWORD", "TREASURE_ADMIN_PASSWORD", "TREASURE_SECRET_KEY", "TREASURE_BACKUP_KEY")]
        if any(value in first.stdout + first.stderr for value in secrets):
            raise AssertionError("Container setup exposed a credential to redirected output")
        before = hashlib.sha256(environment_file.read_bytes()).digest()
        repeated = command(setup, timeout=90)
        assert hashlib.sha256(environment_file.read_bytes()).digest() == before
        if any(value in repeated.stdout + repeated.stderr for value in secrets):
            raise AssertionError("Repeated setup exposed a saved credential")
        hidden = command(setup + ["--show-login"], timeout=90, check=False)
        assert hidden.returncode != 0
        if any(value in hidden.stdout + hidden.stderr for value in secrets):
            raise AssertionError("Saved login was exposed outside an interactive terminal")
        reservation.close()
        started = True
        command(stack + ["up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180"])
        base = f"http://localhost:{port}"
        user = run_check(base, environment_file)
        assert user.get("id")
        # The same user's database survives running initialization again.
        command(stack + ["run", "--rm", "-T", "--no-deps", "--pull", "never", "init"], timeout=90)
        assert run_check(base, environment_file).get("id") == user["id"]
    finally:
        reservation.close()
        if started:
            command(stack + ["down", "--volumes", "--remove-orphans", "--timeout", "10"], timeout=150)
