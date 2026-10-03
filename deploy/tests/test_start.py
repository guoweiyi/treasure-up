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


@pytest.mark.parametrize("light,old", [(True, ["collector", "media-worker"]), (False, ["worker"])])
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
