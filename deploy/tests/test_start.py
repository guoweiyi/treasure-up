"""Assert mode transitions without calling Docker or reading a deployment environment."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
spec = importlib.util.spec_from_file_location("treasure_start", DEPLOY / "start.py")
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)
sys.path.pop(0)


@pytest.mark.parametrize("light,old", [(True, ["download-worker", "media-worker"]), (False, ["worker"])])
def test_replacement_healthy_before_old_workers_stopped(tmp_path, light, old):
    calls = []
    start.start_services(tmp_path, tmp_path / "synthetic.env", light=light, run=lambda command, **kwargs: calls.append(command))
    assert len(calls) == 2
    assert "up" in calls[0] and "--wait" in calls[0]
    assert calls[1][-len(old):] == old and "stop" in calls[1]
    assert "backup-worker" not in calls[1] and "down" not in calls[1] and "--volumes" not in calls[1]
    assert calls[0][:6] == calls[1][:6]


def test_failed_replacement_never_stops_existing_workers(tmp_path):
    calls = []
    def failure(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)
    with pytest.raises(subprocess.CalledProcessError):
        start.start_services(tmp_path, tmp_path / "synthetic.env", light=True, run=failure)
    assert len(calls) == 1


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("build", [False, True])
def test_registry_downloads_finish_before_no_build_start_and_mode_cleanup(tmp_path, light, build):
    calls = []
    root = tmp_path / "release with spaces"
    env = tmp_path / "existing deployment.env"
    start.start_services(root, env, light=light, build=build, prebuilt=True,
                         run=lambda command, **kwargs: calls.append((command, kwargs)))
    commands = [item[0] for item in calls]
    assert len(commands) == 3
    assert commands[0][-3:] == ["pull", "--policy", "always"]
    assert commands[1][-3:] == ["--no-build", "--pull", "never"]
    assert "--build" not in commands[1]
    assert "--wait" in commands[1]
    assert "stop" in commands[2]
    for command, kwargs in calls:
        assert command[3] == str(env.resolve())
        assert str(root / "compose.registry.yaml") in command
        assert kwargs == {"cwd": root, "check": True}
        assert not any(value in command for value in ["down", "--volumes", "--remove-orphans"])
    for command in commands[:2]:
        assert (str(root / "compose.light.yaml") in command) == light
        assert (str(root / "compose.registry.light.yaml") in command) == light
    assert str(root / "compose.registry.light.yaml") in commands[2]
    old_workers = ["download-worker", "media-worker"] if light else ["worker"]
    assert commands[2][-len(old_workers):] == old_workers


@pytest.mark.parametrize("failed_stage", ["pull", "up"])
def test_registry_failure_does_not_continue_to_old_worker_stop(tmp_path, failed_stage):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if failed_stage in command:
            raise subprocess.CalledProcessError(1, command)
    with pytest.raises(subprocess.CalledProcessError):
        start.start_services(tmp_path, tmp_path / "fixture.env", prebuilt=True, light=True, run=run)
    assert len(calls) == (1 if failed_stage == "pull" else 2)
    assert not any("stop" in command for command in calls)


def test_local_no_build_never_pulls_or_implicitly_builds(tmp_path):
    calls = []
    start.start_services(tmp_path, tmp_path / "fixture.env", build=False,
                         run=lambda command, **kwargs: calls.append(command))
    assert len(calls) == 2
    assert calls[0][-3:] == ["--no-build", "--pull", "never"]
    assert "--build" not in calls[0] and "pull" not in calls[0]
    assert not any("registry" in value for command in calls for value in command)


def test_main_prebuilt_preserves_existing_environment_and_passes_flags(tmp_path, monkeypatch):
    env = tmp_path / "existing.env"
    original = "DO_NOT_CHANGE=synthetic-fixture\n"
    env.write_text(original)
    calls = []
    monkeypatch.setattr(sys, "argv", ["start.py", "--prebuilt", "--light", "--no-build", "--env-file", str(env)])
    monkeypatch.setattr(start, "start_services", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert start.main() == 0
    assert env.read_text() == original
    assert calls[0][0][1] == env
    assert calls[0][1] == {"light": True, "build": False, "prebuilt": True}
