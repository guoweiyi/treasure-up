"""Opt-in Docker CLI validation; does not pull images, contact the daemon or start services."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.getenv("TREASURE_RUN_COMPOSE_TESTS") != "1" or not shutil.which("docker")
    or not (ROOT / "compose.yaml").exists(),
    reason="Set TREASURE_RUN_COMPOSE_TESTS=1 on a checkout with the Docker Compose CLI",
)


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("custom", [False, True])
def test_source_free_release_bundle_resolves_registry_images_and_worker_queues(tmp_path, light, custom):
    # Model the release deployment archive: no backend/frontend sources or Dockerfiles.
    for name in ["compose.yaml", "compose.light.yaml", "compose.registry.yaml", "compose.registry.light.yaml"]:
        shutil.copy2(ROOT / name, tmp_path / name)
    fixture = tmp_path / "fixture.env"
    # An upgrade must resolve without retaining the initial administrator password.
    # The application initializer separately requires it for a brand-new database.
    fixture.write_text("POSTGRES_PASSWORD=fixture\nTREASURE_SECRET_KEY=fixture\nTREASURE_BACKUP_KEY=fixture\n")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("TREASURE_", "POSTGRES_", "COMPOSE_"))}
    backend = "ghcr.io/guoweiyi/treasure-up-backend:0.3.2"
    web = "ghcr.io/guoweiyi/treasure-up-web:0.3.2"
    if custom:
        backend = "registry.example.invalid/team/backend@sha256:" + "a" * 64
        web = "registry.example.invalid/team/web@sha256:" + "b" * 64
        environment.update(TREASURE_BACKEND_IMAGE=backend, TREASURE_WEB_IMAGE=web)
    command = ["docker", "compose", "--env-file", str(fixture), "-f", str(tmp_path / "compose.yaml")]
    if light:
        command += ["-f", str(tmp_path / "compose.light.yaml")]
    command += ["-f", str(tmp_path / "compose.registry.yaml")]
    if light:
        command += ["-f", str(tmp_path / "compose.registry.light.yaml")]
    result = subprocess.run(command + ["config", "--format", "json"], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, encoding="utf-8", check=True)
    resolved = json.loads(result.stdout)
    services = resolved["services"]
    assert services["init"]["environment"]["TREASURE_ADMIN_PASSWORD"] == ""
    assert resolved["name"] == "treasure-up"
    for name in ["init", "api", "scheduler", "collector", "backup-worker"]:
        assert services[name]["image"] == backend
    assert services["web"]["image"] == web
    if light:
        assert services["worker"]["image"] == backend
        assert services["worker"]["healthcheck"]["test"][-2:] == ["download", "media"]
        assert "download-worker" not in services and "media-worker" not in services
    else:
        assert "worker" not in services
        for name, queue in [("download-worker", "download"), ("media-worker", "media")]:
            assert services[name]["image"] == backend
            assert services[name]["healthcheck"]["test"][-1] == queue
    assert set(resolved["volumes"]) == {"database", "media", "scratch", "queue", "backup"}
    assert not (tmp_path / "deploy" / "Dockerfile.backend").exists()
