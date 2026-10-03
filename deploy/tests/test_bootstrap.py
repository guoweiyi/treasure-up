"""Deployment helpers are tested on the host; they are not shipped in the backend image."""
import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("treasure_bootstrap", Path(__file__).resolve().parents[2] / "deploy" / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_existing_environment_is_never_read_reset_or_printed(tmp_path):
    destination = tmp_path / ".env"
    original = "TREASURE_ADMIN_PASSWORD=synthetic-existing-secret\n"
    destination.write_text(original)
    output = io.StringIO()
    assert bootstrap.create_environment(destination, stream=output) is False
    assert destination.read_text() == original
    assert "synthetic-existing-secret" not in output.getvalue()


def test_initial_password_only_printed_once_to_interactive_terminal(tmp_path):
    class Terminal(io.StringIO):
        def isatty(self): return True
    destination = tmp_path / ".env"
    terminal = Terminal()
    assert bootstrap.create_environment(destination, stream=terminal)
    fields = dict(line.split("=", 1) for line in destination.read_text().splitlines())
    assert fields["TREASURE_ADMIN_PASSWORD"] in terminal.getvalue()
    assert fields["POSTGRES_PASSWORD"] not in terminal.getvalue()
    assert fields["TREASURE_BACKUP_KEY"] not in terminal.getvalue()
    assert fields["TREASURE_SECRET_KEY"] not in terminal.getvalue()
    assert fields["TREASURE_PASSKEY_RP_ID"] == "localhost"
    second = Terminal()
    assert bootstrap.create_environment(destination, stream=second) is False
    assert fields["TREASURE_ADMIN_PASSWORD"] not in second.getvalue()
    redirected = io.StringIO()
    other = tmp_path / "other.env"
    bootstrap.create_environment(other, stream=redirected)
    other_password = dict(line.split("=", 1) for line in other.read_text().splitlines())["TREASURE_ADMIN_PASSWORD"]
    assert other_password not in redirected.getvalue()


@pytest.mark.parametrize("origin", ["https://site.invalid/path", "http://site.invalid", "https://user:pass@site.invalid", "http://127.0.0.1:8788"])
def test_bootstrap_refuses_ambiguous_or_insecure_passkey_origin(tmp_path, origin):
    with pytest.raises(ValueError):
        bootstrap.create_environment(tmp_path / ".env", origin=origin, stream=io.StringIO())
    assert not (tmp_path / ".env").exists()
