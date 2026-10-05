"""Deployment helpers are tested on the host; they are not shipped in the backend image."""
import importlib.util
import io
import os
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


@pytest.mark.parametrize("origin,expected", [
    ("https://EXAMPLE.com:443/", "https://example.com"),
    ("http://localhost:80", "http://localhost"),
    ("https://example.com:8443", "https://example.com:8443"),
])
def test_origin_matches_browser_default_port_serialization(tmp_path, origin, expected):
    output = tmp_path / "synthetic.env"
    bootstrap.create_environment(output, origin=origin, stream=io.StringIO())
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["TREASURE_PASSKEY_ORIGIN"] == expected


@pytest.mark.parametrize("origin", [
    "https://example.com:0", "https://example.com:65536", "https://example.com:",
    "https://bad host.invalid", "https://a\nb.invalid", "https://@example.com",
    "https://example.com,other.invalid", "https://${HOSTNAME}.invalid", "https://-bad.invalid",
])
def test_invalid_origin_is_rejected_before_creating_secrets(tmp_path, origin):
    output = tmp_path / "synthetic.env"
    with pytest.raises(ValueError):
        bootstrap.create_environment(output, origin=origin, stream=io.StringIO())
    assert not output.exists()


def test_explicit_origin_updates_existing_policy_without_replacing_secrets(tmp_path):
    destination = tmp_path / ".env"
    original = (b"\xef\xbb\xbf# keep custom deployment\r\n"
                b"TREASURE_ADMIN_PASSWORD='synthetic$#password'\r\n"
                b"TREASURE_SECRET_KEY=synthetic-encryption-key\r\n"
                b"TREASURE_BACKUP_KEY=synthetic-backup-key\r\n"
                b"TREASURE_ALLOWED_HOSTS=localhost,127.0.0.1\r\n"
                b"TREASURE_COOKIE_SECURE=false\r\n"
                b"TREASURE_PORT=8788\r\n")
    destination.write_bytes(original)
    output = io.StringIO()
    assert bootstrap.configure_origin(destination, "https://VIDEO.example.com:443/", stream=output)
    updated = destination.read_bytes()
    assert updated.startswith(b"\xef\xbb\xbf# keep custom deployment\r\n")
    for line in original.splitlines():
        if not line.startswith((b"TREASURE_ALLOWED_HOSTS=", b"TREASURE_COOKIE_SECURE=")):
            assert line in updated
    assert b"TREASURE_ALLOWED_HOSTS=video.example.com,localhost,127.0.0.1\r\n" in updated
    assert b"TREASURE_PASSKEY_ORIGIN=https://video.example.com\r\n" in updated
    assert b"TREASURE_PASSKEY_RP_ID=video.example.com\r\n" in updated
    assert b"TREASURE_COOKIE_SECURE=true\r\n" in updated
    assert "synthetic" not in output.getvalue()
    assert bootstrap.configure_origin(destination, "https://video.example.com", stream=output) is False
    assert destination.read_bytes() == updated
    assert not list(tmp_path.glob(".env.treasure-site-*"))
    if os.name != "nt":
        assert destination.stat().st_mode & 0o077 == 0


def test_origin_preserves_explicit_port_and_replaces_exported_settings(tmp_path):
    destination = tmp_path / ".env"
    destination.write_text("export TREASURE_PASSKEY_ORIGIN='https://old.example.com'\nOTHER=keep", encoding="utf-8")
    bootstrap.configure_origin(destination, "https://tunnel.example.com:18443", stream=io.StringIO())
    contents = destination.read_text(encoding="utf-8")
    assert "TREASURE_PASSKEY_ORIGIN=https://tunnel.example.com:18443\n" in contents
    assert "TREASURE_PASSKEY_RP_ID=tunnel.example.com\n" in contents
    assert "OTHER=keep\n" in contents
    assert "old.example.com" not in contents


@pytest.mark.parametrize("content,origin", [
    ("OTHER=keep\n", "https://bad.example.com/path"),
    ("TREASURE_ALLOWED_HOSTS=one\nTREASURE_ALLOWED_HOSTS=two\n", "https://video.example.com"),
    ('SECRET="multiline\nTREASURE_ALLOWED_HOSTS=inside-secret\n"\n', "https://video.example.com"),
])
def test_ambiguous_environment_or_origin_is_not_modified(tmp_path, content, origin):
    destination = tmp_path / ".env"
    destination.write_text(content, encoding="utf-8")
    original = destination.read_bytes()
    with pytest.raises(ValueError):
        bootstrap.configure_origin(destination, origin, stream=io.StringIO())
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".env.treasure-site-*"))


def test_failed_atomic_replace_preserves_existing_environment(tmp_path, monkeypatch):
    destination = tmp_path / ".env"
    destination.write_bytes(b"SECRET=synthetic-secret\n")
    def fail(*args):
        raise OSError("synthetic replace failure")
    monkeypatch.setattr(bootstrap.os, "replace", fail)
    with pytest.raises(OSError):
        bootstrap.configure_origin(destination, "https://video.example.com", stream=io.StringIO())
    assert destination.read_bytes() == b"SECRET=synthetic-secret\n"
    assert not list(tmp_path.glob(".env.treasure-site-*"))


def test_http_tunnel_requires_explicit_opt_in_and_disables_passkeys(tmp_path):
    destination = tmp_path / ".env"
    destination.write_bytes(b"SECRET=synthetic-secret\n")
    with pytest.raises(ValueError, match="--allow-http"):
        bootstrap.configure_origin(destination, "http://tunnel.example.com", stream=io.StringIO())
    assert destination.read_bytes() == b"SECRET=synthetic-secret\n"
    bootstrap.configure_origin(destination, "http://tunnel.example.com:8080", allow_http=True, stream=io.StringIO())
    contents = destination.read_text(encoding="utf-8")
    assert "TREASURE_PASSKEY_ORIGIN=http://tunnel.example.com:8080" in contents
    assert "TREASURE_COOKIE_SECURE=false" in contents
    assert "TREASURE_PASSKEYS_ENABLED=false" in contents
    assert "TREASURE_ALLOWED_HOSTS=tunnel.example.com,localhost,127.0.0.1" in contents
    bootstrap.configure_origin(destination, "https://tunnel.example.com", stream=io.StringIO())
    contents = destination.read_text(encoding="utf-8")
    assert "TREASURE_COOKIE_SECURE=true" in contents
    assert "TREASURE_PASSKEYS_ENABLED=true" in contents


def test_new_http_tunnel_configuration_requires_the_same_explicit_opt_in(tmp_path):
    destination = tmp_path / ".env"
    bootstrap.create_environment(destination, origin="http://tunnel.example.com", allow_http=True, stream=io.StringIO())
    contents = destination.read_text(encoding="utf-8")
    assert "TREASURE_COOKIE_SECURE=false" in contents
    assert "TREASURE_PASSKEYS_ENABLED=false" in contents


def test_symlink_environment_is_rejected_without_changing_target(tmp_path):
    target = tmp_path / "real.env"
    target.write_bytes(b"SECRET=synthetic-secret\n")
    link = tmp_path / "alias.env"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this host")
    with pytest.raises(ValueError, match="symlink"):
        bootstrap.configure_origin(link, "https://video.example.com", stream=io.StringIO())
    assert target.read_bytes() == b"SECRET=synthetic-secret\n"
