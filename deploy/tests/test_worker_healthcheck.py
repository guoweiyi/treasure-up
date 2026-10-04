"""Health checks must identify the replacement consumer, not any healthy old worker."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("worker_healthcheck", Path(__file__).resolve().parents[1] / "worker_healthcheck.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


@pytest.mark.parametrize("reply,expected", [
    (None, False),
    ({"celery@old": [{"name": "download"}, {"name": "media"}]}, False),
    ({"celery@new": [{"name": "collector"}]}, False),
    ({"celery@new": [{"name": "download"}]}, False),
    ({"celery@new": [{"name": "download"}, {"name": "media"}]}, True),
])
def test_only_requested_node_with_all_required_queues_is_healthy(reply, expected):
    calls = []
    def inspect(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(active_queues=lambda: reply)
    app = SimpleNamespace(control=SimpleNamespace(inspect=inspect))
    assert health.worker_ready(app, ["download", "media"], hostname="new") is expected
    assert calls == [{"destination": ["celery@new"], "timeout": 3}]


def test_broker_failure_exits_unhealthy_without_exposing_connection(monkeypatch, capsys):
    app = SimpleNamespace(celery=object())
    monkeypatch.setitem(__import__("sys").modules, "app.worker", app)
    def fail(*args):
        raise RuntimeError("synthetic-private-broker-url")
    monkeypatch.setattr(health, "worker_ready", fail)
    assert health.main() == 1
    assert "synthetic-private-broker-url" not in capsys.readouterr().err
