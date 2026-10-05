import json
from pathlib import Path

from app.ingest.diagnostics import summarize


def _failure(message):
    namespace = {"credential": message}
    path = Path(__file__).resolve().parents[1] / "app" / "ingest" / "runner.py"
    try:
        exec(compile("def process():\n    raise ValueError(credential)\nprocess()", str(path), "exec"), namespace)
    except ValueError as error:
        return summarize(error)


def test_diagnostic_retains_location_not_error_message_or_locals():
    value = _failure("SESSDATA=private-cookie; https://source.example/?token=secret")
    assert value["type"] == "ValueError"
    assert value["frames"][-1] == {"module": "app.ingest.runner", "function": "process", "line": 2}
    encoded = json.dumps(value)
    assert not any(word in encoded for word in ("private-cookie", "source.example", "credential", "token=", str(Path.cwd())))


def test_diagnostic_groups_same_failure_without_hashing_private_inputs():
    assert _failure("first secret") == _failure("different secret")
    assert len(_failure("secret")["fingerprint"]) == 16
