"""Opt-in installation acceptance test with local images and isolated Docker volumes.

Only a source-free Compose file is given to Docker. The test never mounts a host
source tree, credentials file or Docker socket into the application containers.
"""
import hashlib
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from uuid import uuid4

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
ROOT = DEPLOY.parent
sys.path.insert(0, str(DEPLOY))
from check_service import NoRedirect, run_check
from compose_config import compose_content

pytestmark = pytest.mark.skipif(
    os.environ.get("TREASURE_RUN_INSTALL_TESTS") != "1",
    reason="Set TREASURE_RUN_INSTALL_TESTS=1 after building local backend/web/setup images",
)


def _redact(output, environment_file):
    if environment_file.is_file():
        for line in environment_file.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and value and key.endswith(("_PASSWORD", "_KEY")):
                output = output.replace(value, "[redacted]")
    return output


def test_fresh_install_uses_only_compose_and_preserves_initialization(tmp_path):
    version = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))["version"]
    images = {component: os.environ.get(
        f"TREASURE_INSTALL_{component.upper()}_IMAGE", f"treasure-up-{component}:{version}"
    ) for component in ("backend", "web", "setup")}
    project = "treasure-install-test-" + uuid4().hex[:16]
    config_volume, migrated_volume = project + "-config", project + "-migrated-config"
    reader, environment_file = project + "-reader", tmp_path / ".env"
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("TREASURE_", "POSTGRES_", "COMPOSE_"))}
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://localhost:{port}"
    environment.update(TREASURE_PORT=str(port), TREASURE_CONFIG_VOLUME=config_volume, TREASURE_PUBLIC_ORIGIN=base)
    compose_file, empty_env = tmp_path / "compose.yaml", tmp_path / "compose.env"
    compose_file.write_text(compose_content(ROOT, images), encoding="utf-8")
    empty_env.write_text("", encoding="utf-8")
    compose = ["compose", "--project-name", project, "--env-file", str(empty_env), "-f", str(compose_file)]
    up = [*compose, "up", "--detach", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180"]
    setup = [*compose, "run", "--rm", "--no-deps", "--pull", "never", "-T", "setup"]

    def command(arguments, *, timeout=240, check=True):
        result = subprocess.run(["docker", *arguments], cwd=tmp_path, env=environment,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if check and result.returncode:
            output = _redact(result.stdout + result.stderr, environment_file)
            raise AssertionError("Installation Docker command failed:\n" + output[-8000:])
        return result

    def snapshot(volume=config_volume):
        # The stopped reader copies the private file without putting its contents in logs.
        command(["create", "--name", reader, "--pull", "never", "--network", "none",
                 "--mount", f"type=volume,source={volume},target=/config,readonly", images["setup"]])
        try:
            command(["cp", reader + ":/config/.", str(tmp_path)])
        finally:
            command(["rm", "--force", reader], check=False)

    def no_secret_output(result, secrets):
        if any(value in result.stdout + result.stderr for value in secrets):
            raise AssertionError("A saved credential appeared in non-interactive output")

    try:
        assert not command(["ps", "--all", "--quiet", "--filter", f"label=com.docker.compose.project={project}"]).stdout.strip()
        first = command(up, timeout=300)
        snapshot()
        state = json.loads((tmp_path / "installation.json").read_text(encoding="utf-8"))
        assert state["attempted_version"] == version and state["config_format"] == 1
        settings = dict(line.split("=", 1) for line in environment_file.read_text(encoding="utf-8").splitlines())
        secrets = [settings[key] for key in ("POSTGRES_PASSWORD", "TREASURE_ADMIN_PASSWORD", "TREASURE_SECRET_KEY", "TREASURE_BACKUP_KEY")]
        no_secret_output(first, secrets)
        before = hashlib.sha256(environment_file.read_bytes()).digest()
        user = run_check(base, environment_file)
        assert user.get("id")

        # Model a TLS-terminating proxy over the isolated loopback connection.
        # Send the Secure cookie manually: an HTTP CookieJar correctly withholds it.
        proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

        def proxy_request(path, body=None, *, host="local.gwy.fun", origin="https://local.gwy.fun", cookie="", csrf=""):
            headers = {"Host": host, "Origin": origin, "X-Forwarded-Proto": "https", "Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if cookie:
                headers["Cookie"] = cookie
            if csrf:
                headers["X-CSRF-Token"] = csrf
            request = urllib.request.Request(base + path, headers=headers,
                                             data=json.dumps(body).encode() if body is not None else None)
            try:
                response = proxy.open(request, timeout=15)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                content = response.read(1024 * 1024)
                return response.status, response.headers, content

        credentials = {"username": settings["TREASURE_ADMIN_USERNAME"], "password": settings["TREASURE_ADMIN_PASSWORD"]}
        status, _headers, _body = proxy_request("/api/v1/auth/login", credentials, origin="https://untrusted.invalid")
        assert status == 403
        status, _headers, _body = proxy_request("/health", host="untrusted.invalid")
        assert status == 400
        status, headers, body = proxy_request("/api/v1/auth/login", credentials)
        if status != 200:
            raise AssertionError("The trusted HTTPS proxy origin could not sign in")
        login = json.loads(body)
        cookies = SimpleCookie()
        for value in headers.get_all("Set-Cookie", []):
            cookies.load(value)
        session = cookies.get("treasure_session")
        if session is None or not session["secure"] or not login.get("csrf_token"):
            raise AssertionError("The trusted HTTPS proxy login did not issue a Secure session")
        session_header = "treasure_session=" + session.value
        try:
            status, _headers, _body = proxy_request("/api/v1/auth/me", cookie=session_header)
            assert status == 200
        finally:
            status, _headers, _body = proxy_request("/api/v1/auth/logout", {}, cookie=session_header, csrf=login["csrf_token"])
            assert status == 200
        status, _headers, _body = proxy_request("/api/v1/auth/me", cookie=session_header)
        assert status == 401
        del credentials, login, cookies, session, session_header, headers, body

        containers = command(["ps", "--all", "--quiet", "--filter", f"label=com.docker.compose.project={project}"]).stdout.split()
        inspected = json.loads(command(["inspect", *containers]).stdout)
        for container in inspected:
            name = container["Config"]["Labels"]["com.docker.compose.service"]
            assert not container["HostConfig"]["Privileged"]
            assert all(mount["Type"] == "volume" and mount["Destination"] != "/var/run/docker.sock"
                       for mount in container["Mounts"])
            if any(value in json.dumps(container["Config"]["Env"]) for value in secrets):
                raise AssertionError("A credential was exposed through Docker container configuration")
            if name == "api":
                destinations = {mount["Destination"] for mount in container["Mounts"]}
                assert not destinations & {"/config", "/run/treasure-init", "/run/treasure-backup"}
                assert "/run/treasure" in destinations
                assert not any(item.startswith("TREASURE_ROLE_CONFIG_FILE=") for item in container["Config"]["Env"])
            if name == "setup":
                assert container["HostConfig"]["ReadonlyRootfs"]
                assert container["HostConfig"]["NetworkMode"] == "none"
                assert container["HostConfig"]["LogConfig"]["Type"] == "none"
        command([*compose, "exec", "-T", "api", "python", "-c",
                 "import json; from pathlib import Path; p=Path('/run/treasure/runtime.json'); "
                 "v=json.loads(p.read_text()); assert p.stat().st_uid==10001 and p.stat().st_mode & 0o777==0o400; "
                 "assert not {'TREASURE_ADMIN_PASSWORD','TREASURE_BACKUP_KEY'} & v.keys(); "
                 "assert not Path('/config/.env').exists() and not Path('/run/treasure-init/role.json').exists() "
                 "and not Path('/run/treasure-backup/role.json').exists()"])

        # Default commands after first installation must retain the saved origin and keys.
        environment.pop("TREASURE_PUBLIC_ORIGIN")
        repeated = command([*up, "--force-recreate"], timeout=300)
        snapshot()
        assert hashlib.sha256(environment_file.read_bytes()).digest() == before
        no_secret_output(repeated, secrets)
        assert run_check(base, environment_file).get("id") == user["id"]
        hidden = command([*setup, "--show-login"], timeout=90, check=False)
        assert hidden.returncode != 0
        no_secret_output(hidden, secrets)
        # A Docker TTY is used only for this explicit private display check. Its
        # captured response is never printed or included in assertion messages.
        shown = command([*compose, "run", "--rm", "--no-deps", "--pull", "never",
                         "--interactive=false", "--no-TTY=false", "setup", "--show-login"], timeout=90, check=False)
        if shown.returncode or settings["TREASURE_ADMIN_PASSWORD"] not in shown.stdout:
            raise AssertionError("Interactive setup did not display the saved initial login")
        del shown
        no_secret_output(command([*compose, "logs", "--no-color"], timeout=90), secrets)

        # An empty configuration volume must refuse existing database files.
        # Explicitly import the original .env without changing the database.
        environment["TREASURE_CONFIG_VOLUME"] = migrated_volume
        denied = command(setup, timeout=90, check=False)
        assert denied.returncode != 0 and "Existing database data" in denied.stdout + denied.stderr
        no_secret_output(denied, secrets)
        imported = command([*compose, "run", "--rm", "--no-deps", "--pull", "never", "-T",
                            "--volume", f"{config_volume}:/previous:ro", "setup", "--import-env", "/previous/.env"], timeout=90)
        no_secret_output(imported, secrets)
        no_secret_output(command([*up, "--force-recreate"], timeout=300), secrets)
        snapshot(migrated_volume)
        assert hashlib.sha256(environment_file.read_bytes()).digest() == before
        assert run_check(base, environment_file).get("id") == user["id"]

        # Only temporary installer metadata is changed to simulate a newer
        # migration attempt; the application's database is never modified.
        command(["run", "--rm", "--name", reader, "--pull", "never", "--network", "none", "--entrypoint", "python",
                 "--mount", f"type=volume,source={migrated_volume},target=/config", images["setup"],
                 "-c", "import json; from pathlib import Path; p=Path('/config/installation.json'); "
                 "s=json.loads(p.read_text()); s['attempted_version']='999.0.0'; p.write_text(json.dumps(s))"])
        downgrade = command(setup, timeout=90, check=False)
        assert downgrade.returncode != 0 and "would downgrade" in downgrade.stdout + downgrade.stderr
        no_secret_output(downgrade, secrets)
        assert run_check(base, environment_file).get("id") == user["id"]
    finally:
        command(["rm", "--force", reader], check=False)
        # Clean only this randomly named project, including one-off setup jobs.
        for list_args, delete_args in (
            (["ps", "--all", "--quiet"], ["rm", "--force"]),
            (["network", "ls", "--quiet"], ["network", "rm"]),
            (["volume", "ls", "--quiet"], ["volume", "rm"]),
        ):
            owned = command([*list_args, "--filter", f"label=com.docker.compose.project={project}"]).stdout.split()
            if owned:
                command([*delete_args, *owned], timeout=150)
        command(["volume", "rm", config_volume, migrated_volume], check=False)
