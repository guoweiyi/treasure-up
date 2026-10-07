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
    images = {component: os.environ.get(
        f"TREASURE_INSTALL_{component.upper()}_IMAGE", f"treasure-up-{component}:{version}"
    ) for component in ("backend", "web")}
    project = "treasure-install-test-" + uuid4().hex[:16]
    config_volume, installer, reader = project + "-config", project + "-installer", project + "-reader"
    migrated_volume = project + "-migrated-config"
    environment_file = tmp_path / ".env"
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
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    setup = ["run", "--rm", "--name", installer, "--user", "0", "--pull", "never", "--network", "none",
             "--mount", "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock",
             "--mount", f"type=volume,source={config_volume},target=/config",
             images["backend"], "python", "/app/install.py", "--project-name", project,
             "--backend-image", images["backend"], "--web-image", images["web"], "--no-pull", "--port", str(port)]

    def snapshot(volume=config_volume):
        # Copy config privately for HTTP assertions; never print its secrets to Docker logs.
        command(["create", "--name", reader, "--user", "0", "--pull", "never",
                 "--mount", f"type=volume,source={volume},target=/config,readonly", images["backend"]])
        try:
            command(["cp", reader + ":/config/.", str(tmp_path)])
        finally:
            command(["rm", "--force", reader], check=False)

    try:
        # No downloaded archive, host configuration or source tree is mounted.
        assert not any(tmp_path.iterdir())
        first = command(setup, timeout=300)
        snapshot()
        state = json.loads((tmp_path / "installation.json").read_text(encoding="utf-8"))
        assert state["completed_version"] == version and state["project"] == project
        assert state["mode"] == "light"
        config = yaml.safe_load((tmp_path / "compose.yaml").read_text(encoding="utf-8"))
        assert all(not service.get("build") for service in config["services"].values())
        assert "/var/run/docker.sock" not in (tmp_path / "compose.yaml").read_text(encoding="utf-8")
        settings = dict(line.split("=", 1) for line in environment_file.read_text(encoding="utf-8").splitlines())
        secrets = [settings[key] for key in ("POSTGRES_PASSWORD", "TREASURE_ADMIN_PASSWORD", "TREASURE_SECRET_KEY", "TREASURE_BACKUP_KEY")]
        if any(value in first.stdout + first.stderr for value in secrets):
            raise AssertionError("Container setup exposed a credential to redirected output")
        before = hashlib.sha256(environment_file.read_bytes()).digest()
        base = f"http://localhost:{port}"
        user = run_check(base, environment_file)
        assert user.get("id")
        repeated = command(setup, timeout=300)
        snapshot()
        assert hashlib.sha256(environment_file.read_bytes()).digest() == before
        if any(value in repeated.stdout + repeated.stderr for value in secrets):
            raise AssertionError("Repeated setup exposed a saved credential")
        assert run_check(base, environment_file).get("id") == user["id"]
        hidden = command(setup + ["--show-login"], timeout=90, check=False)
        assert hidden.returncode != 0
        if any(value in hidden.stdout + hidden.stderr for value in secrets):
            raise AssertionError("Saved login was exposed outside an interactive terminal")
        # A different empty config volume must not silently regenerate keys for
        # the existing database. Explicit import is the supported migration.
        migration = setup.copy()
        migration[migration.index(f"type=volume,source={config_volume},target=/config")] = \
            f"type=volume,source={migrated_volume},target=/config"
        denied = command(migration, timeout=90, check=False)
        assert denied.returncode != 0 and "Existing project data" in denied.stdout + denied.stderr
        position = migration.index(images["backend"])
        migration[position:position] = ["--mount", f"type=volume,source={config_volume},target=/previous,readonly"]
        imported = command(migration + ["--import-env", "/previous/.env"], timeout=300)
        if any(value in imported.stdout + imported.stderr for value in secrets):
            raise AssertionError("Configuration migration exposed a saved credential")
        snapshot(migrated_volume)
        assert hashlib.sha256(environment_file.read_bytes()).digest() == before
        assert run_check(base, environment_file).get("id") == user["id"]
        # A failed newer deployment may already have migrated the database.
        # Mark only temporary installer metadata; never change the actual DB.
        command(["run", "--rm", "--user", "0", "--pull", "never", "--network", "none",
                 "--mount", f"type=volume,source={migrated_volume},target=/config", images["backend"],
                 "python", "-c", "import json; from pathlib import Path; p=Path('/config/installation.json'); "
                 "s=json.loads(p.read_text()); s['attempted_version']='999.0.0'; p.write_text(json.dumps(s))"])
        downgrade = command(migration, timeout=90, check=False)
        assert downgrade.returncode != 0 and "would downgrade" in downgrade.stdout + downgrade.stderr
        assert run_check(base, environment_file).get("id") == user["id"]
    finally:
        # Kill a timed-out installer before removing only this random test project's resources.
        command(["rm", "--force", installer, reader], check=False)
        for resource, list_args, delete_args in (
            ("containers", ["ps", "--all", "--quiet"], ["rm", "--force"]),
            ("networks", ["network", "ls", "--quiet"], ["network", "rm"]),
            ("volumes", ["volume", "ls", "--quiet"], ["volume", "rm"]),
        ):
            owned = command([*list_args, "--filter", f"label=com.docker.compose.project={project}"]).stdout.split()
            if owned:
                command([*delete_args, *owned], timeout=150)
        command(["volume", "rm", config_volume, migrated_volume], check=False)
