"""Installation must preserve data credentials and never leak them into CI output."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys

import pytest


spec = importlib.util.spec_from_file_location("deployment_setup", Path(__file__).parents[1] / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class Terminal(io.StringIO):
    def isatty(self):
        return True


def environment(path):
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


def test_first_install_generates_distinct_secrets_without_redirected_password(tmp_path):
    path, output = tmp_path / ".env", io.StringIO()
    assert setup.create_environment(path, stream=output)
    values = environment(path)
    secrets = [values[key] for key in ("POSTGRES_PASSWORD", "TREASURE_ADMIN_PASSWORD", "TREASURE_SECRET_KEY", "TREASURE_BACKUP_KEY")]
    assert len(set(secrets)) == 4 and all(len(value) >= 32 for value in secrets)
    assert all(value not in output.getvalue() for value in secrets)
    assert values["TREASURE_ALLOWED_HOSTS"] == "localhost,127.0.0.1,local.gwy.fun"
    assert values["TREASURE_TRUSTED_ORIGINS"] == "https://local.gwy.fun"
    assert values["TREASURE_COOKIE_SECURE"] == "false"
    assert values["TREASURE_PASSKEY_ORIGIN"] == "http://localhost:8788"
    assert values["TREASURE_PASSKEYS_ENABLED"] == "true"


def test_rerun_preserves_entire_existing_environment_and_never_reprints_secrets(tmp_path):
    path = tmp_path / ".env"
    setup.create_environment(path, stream=io.StringIO())
    original = path.read_bytes()
    output = Terminal()
    assert not setup.create_environment(path, origin="https://new.example.com", stream=output)
    assert path.read_bytes() == original
    assert environment(path)["TREASURE_ADMIN_PASSWORD"] not in output.getvalue()


def test_private_terminal_can_read_initial_login_without_changing_any_file(tmp_path):
    path, output = tmp_path / ".env", Terminal()
    setup.create_environment(path, admin_username="xiaoyun", stream=output)
    initial = path.read_bytes()
    password = environment(path)["TREASURE_ADMIN_PASSWORD"]
    assert password in output.getvalue()
    output = Terminal()
    setup.show_login(path, stream=output)
    assert password in output.getvalue() and "xiaoyun" in output.getvalue()
    assert "does not reset" in output.getvalue()
    assert path.read_bytes() == initial


def test_login_rejects_pipes_before_reading_secrets(tmp_path):
    path = tmp_path / ".env"
    path.write_text("TREASURE_ADMIN_PASSWORD=do-not-leak\n")
    output = io.StringIO()
    with pytest.raises(ValueError, match="interactive terminal"):
        setup.show_login(path, stream=output)
    assert not output.getvalue()


@pytest.mark.parametrize("content", [
    "TREASURE_ADMIN_PASSWORD=\n",
    "TREASURE_ADMIN_PASSWORD=one\nTREASURE_ADMIN_PASSWORD=two\n",
    "TREASURE_ADMIN_PASSWORD='unterminated\n",
    "TREASURE_ADMIN_PASSWORD=secret\x1b[2J\n",
])
def test_unreadable_or_ambiguous_saved_login_never_prints_partial_values(tmp_path, content):
    path, output = tmp_path / ".env", Terminal()
    path.write_text(content)
    with pytest.raises(ValueError):
        setup.show_login(path, stream=output)
    assert not output.getvalue()


def test_origin_update_preserves_credentials_and_configures_all_proxy_related_policy(tmp_path):
    path = tmp_path / ".env"
    setup.create_environment(path, stream=io.StringIO())
    before = environment(path)
    assert setup.configure_origin(path, "https://video.example.com:443/", stream=io.StringIO())
    after = environment(path)
    for key in ("POSTGRES_PASSWORD", "TREASURE_ADMIN_PASSWORD", "TREASURE_SECRET_KEY", "TREASURE_BACKUP_KEY", "TREASURE_PORT"):
        assert before[key] == after[key]
    assert after["TREASURE_ALLOWED_HOSTS"] == "video.example.com,localhost,127.0.0.1,local.gwy.fun"
    assert after["TREASURE_PASSKEY_RP_ID"] == "video.example.com"
    assert after["TREASURE_PASSKEY_ORIGIN"] == "https://video.example.com"
    assert after["TREASURE_PASSKEYS_ENABLED"] == after["TREASURE_COOKIE_SECURE"] == "true"
    assert not setup.configure_origin(path, "https://video.example.com", stream=io.StringIO())


@pytest.mark.parametrize("origin", [
    "http://video.example.com", "https://0.0.0.0", "https://user:password@video.example.com",
    "https://video.example.com/base", "https://video.example.com?token=secret", "https://video.example.com\n",
])
def test_invalid_origin_does_not_create_or_mutate_environment(tmp_path, origin):
    path = tmp_path / ".env"
    with pytest.raises(ValueError):
        setup.create_environment(path, origin=origin, stream=io.StringIO())
    assert not path.exists()
    setup.create_environment(path, stream=io.StringIO())
    before = path.read_bytes()
    with pytest.raises(ValueError):
        setup.configure_origin(path, origin, stream=io.StringIO())
    assert path.read_bytes() == before


def test_http_tunnel_requires_explicit_opt_in_and_disables_secure_only_features(tmp_path):
    path = tmp_path / ".env"
    setup.create_environment(path, origin="http://video.example.com:8788", allow_http=True, stream=io.StringIO())
    values = environment(path)
    assert values["TREASURE_PASSKEYS_ENABLED"] == values["TREASURE_COOKIE_SECURE"] == "false"


@pytest.mark.parametrize("origin", ["http://192.168.1.20:8788", "http://10.0.0.4:8788", "http://172.16.0.8:8788", "http://127.0.0.1:8788"])
def test_lan_password_login_does_not_offer_unsupported_ip_passkeys(tmp_path, origin):
    path = tmp_path / ".env"
    setup.create_environment(path, origin=origin, stream=io.StringIO())
    values = environment(path)
    assert values["TREASURE_PASSKEY_ORIGIN"] == origin
    assert values["TREASURE_PASSKEYS_ENABLED"] == values["TREASURE_COOKIE_SECURE"] == "false"
    assert values["TREASURE_PASSKEY_RP_ID"] in values["TREASURE_ALLOWED_HOSTS"].split(",")


def test_https_ip_keeps_secure_cookie_but_disables_passkeys(tmp_path):
    path = tmp_path / ".env"
    setup.create_environment(path, origin="https://192.168.1.20", stream=io.StringIO())
    values = environment(path)
    assert values["TREASURE_PASSKEYS_ENABLED"] == "false"
    assert values["TREASURE_COOKIE_SECURE"] == "true"


def test_nas_setup_cli_then_explicit_binding_change_preserve_credentials_and_origin(tmp_path):
    path = tmp_path / ".env"
    command = [sys.executable, str(Path(setup.__file__).resolve()), "--output", str(path)]
    created = subprocess.run(command + ["--origin", "http://192.168.1.20:8788", "--bind-address", "0.0.0.0"],
                             capture_output=True, text=True, check=True)
    before = environment(path)
    assert before["TREASURE_BIND_ADDRESS"] == "0.0.0.0"
    assert before["TREASURE_ADMIN_PASSWORD"] not in created.stdout + created.stderr
    updated = subprocess.run(command + ["--bind-address", "127.0.0.1"], capture_output=True, text=True, check=True)
    after = environment(path)
    assert {**before, "TREASURE_BIND_ADDRESS": "127.0.0.1"} == after
    assert before["TREASURE_ADMIN_PASSWORD"] not in updated.stdout + updated.stderr


@pytest.mark.parametrize("address", ["localhost", "::", "0.0.0.0:8000", "224.0.0.1", "0.0.0.0\nOTHER=true"])
def test_invalid_listener_never_creates_or_partially_changes_site(tmp_path, address):
    path = tmp_path / ".env"
    with pytest.raises(ValueError):
        setup.create_environment(path, bind_address=address, stream=io.StringIO())
    assert not path.exists()
    setup.create_environment(path, stream=io.StringIO())
    before = path.read_bytes()
    with pytest.raises(ValueError):
        setup.configure_site(path, origin="https://new.example.com", bind_address=address, stream=io.StringIO())
    assert path.read_bytes() == before


def test_origin_duplicate_setting_is_rejected_without_touching_secrets(tmp_path):
    path = tmp_path / ".env"
    setup.create_environment(path, stream=io.StringIO())
    with path.open("a") as output:
        output.write("TREASURE_ALLOWED_HOSTS=another.example.com\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Duplicate"):
        setup.configure_origin(path, "https://video.example.com", stream=io.StringIO())
    assert path.read_bytes() == before


def test_updating_an_older_install_does_not_add_the_new_default_alias(tmp_path):
    path = tmp_path / ".env"
    setup.create_environment(path, stream=io.StringIO())
    original = path.read_text(encoding="utf-8").replace(",local.gwy.fun", "").replace(
        "TREASURE_TRUSTED_ORIGINS=https://local.gwy.fun\n", "")
    path.write_text(original, encoding="utf-8")
    setup.configure_origin(path, "https://private.example.com", stream=io.StringIO())
    values = environment(path)
    assert values["TREASURE_ALLOWED_HOSTS"] == "private.example.com,localhost,127.0.0.1"
    assert "TREASURE_TRUSTED_ORIGINS" not in values
