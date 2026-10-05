"""Exercise the real app behind internal HTTP without altering imported app settings.

Each case imports the application in a fresh interpreter with a temporary SQLite
database and synthetic credentials. No existing .env, database or proxy is used.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest


BACKEND = Path(__file__).resolve().parents[1]
SCENARIO = r'''
import os
import sys
from http.cookies import SimpleCookie

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.db import engine, SessionLocal
from app.main import app
from app.models import Base, User
from app.security import COOKIE_NAME, hash_password

origin = os.environ["TREASURE_PASSKEY_ORIGIN"]
hostname = os.environ["TREASURE_PASSKEY_RP_ID"]
password = "synthetic-proxy-password-123"
Base.metadata.create_all(engine)
with SessionLocal() as db:
    db.add(User(username="proxy-fixture", password_hash=hash_password(password), role="admin"))
    db.commit()

# Mirror the image's Uvicorn proxy-header layer. The inner nginx overwrites
# X-Forwarded-Proto with http; the browser supplies the configured public Origin.
proxy = ProxyHeadersMiddleware(app, trusted_hosts="*")
with TestClient(proxy, base_url="http://" + hostname, follow_redirects=False) as client:
    client.headers["X-Forwarded-Proto"] = "http"
    body = {"username": "proxy-fixture", "password": password}
    scenario = sys.argv[1]
    if scenario in {"login", "public_http"}:
        secure = scenario == "login"
        capability = client.get("/api/v1/server")
        assert capability.status_code == 200
        assert capability.json()["application"] == "treasure-up"
        passkeys = client.get("/api/v1/auth/passkeys/capabilities", params={"origin": origin})
        assert passkeys.status_code == 200
        assert passkeys.json()["enabled"] is secure
        assert passkeys.json()["available"] is secure
        assert passkeys.json()["origin"] == origin
        assert passkeys.json()["rp_id"] == hostname
        if not secure:
            assert passkeys.json()["password_login"] is True
            assert passkeys.json()["reason"]
            assert client.post("/api/v1/auth/passkeys/login/options", json={},
                               headers={"Origin": origin}).status_code == 503
            assert client.post("/api/v1/auth/login", json=body,
                               headers={"Origin": "http://untrusted.invalid"}).status_code == 403
            for forwarded in ({}, {"X-Forwarded-Host": hostname},
                              {"X-Forwarded-Host": hostname, "X-Forwarded-Proto": "https",
                               "Forwarded": 'host="' + hostname + '";proto=https'}):
                assert client.get("/api/v1/server", headers={
                    "Host": "untrusted.invalid", **forwarded,
                }).status_code == 400
            assert client.get("/api/v1/server", headers={
                "X-Forwarded-Host": "untrusted.invalid",
            }).status_code == 200

        response = client.post("/api/v1/auth/login", json=body, headers={"Origin": origin})
        assert response.status_code == 200
        cookies = SimpleCookie()
        cookies.load(response.headers["set-cookie"])
        cookie = cookies[COOKIE_NAME]
        assert bool(cookie["secure"]) is secure and cookie["httponly"]
        assert cookie["samesite"].lower() == "strict" and cookie["path"] == "/"
        csrf = response.json()["csrf_token"]

        # TestClient correctly withholds Secure cookies from an HTTP URL. Copy
        # only this fixture's freshly minted cookie to model the TLS terminator
        # forwarding a browser cookie received over external HTTPS.
        headers = {"Origin": origin}
        if secure:
            headers["Cookie"] = COOKIE_NAME + "=" + cookie.value
        # Public HTTP uses its normal cookie jar; an accidental Secure flag
        # would make this password session unusable in a real HTTP browser.
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 403
        assert client.post("/api/v1/auth/logout", headers={**headers, "X-CSRF-Token": "wrong"}).status_code == 403
        assert client.post("/api/v1/auth/logout", headers={**headers, "X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401

    elif scenario == "host":
        # Forwarded host metadata cannot authorize an unknown actual Host.
        for forwarded in ({}, {"X-Forwarded-Host": hostname},
                          {"X-Forwarded-Host": hostname, "X-Forwarded-Proto": "https",
                           "Forwarded": 'host="' + hostname + '";proto=https'}):
            response = client.get("/api/v1/server", headers={"Host": "untrusted.invalid", **forwarded})
            assert response.status_code == 400
        # Nor can an attacker-provided forwarded host replace an allowed Host.
        assert client.get("/api/v1/server", headers={"X-Forwarded-Host": "untrusted.invalid"}).status_code == 200

    elif scenario == "origin":
        headers = {"Origin": "https://untrusted.invalid", "X-Forwarded-Host": "untrusted.invalid"}
        assert client.post("/api/v1/auth/login", json=body, headers=headers).status_code == 403
        assert client.post("/api/v1/auth/passkeys/login/options", json={}, headers=headers).status_code == 403
        # Guest playback creation retains its own origin boundary too; rejection
        # occurs before any playback session or media lookup can be created.
        assert client.post("/api/v1/playback-sessions",
                           json={"part_id": "00000000-0000-4000-8000-000000000001"},
                           headers=headers).status_code == 403
    else:
        raise AssertionError("Unknown fixture scenario")
engine.dispose()
'''


@pytest.mark.parametrize("scenario", ["login", "host", "origin", "public_http"])
def test_configured_public_origin_over_internal_http(tmp_path, scenario):
    env = os.environ.copy()
    # Keep only this scenario's synthetic application settings; do not inherit
    # deployment credentials, key file paths or unrelated security settings.
    for key in list(env):
        if key.startswith("TREASURE_"):
            del env[key]
    env.update({
        "PYTHONPATH": str(BACKEND),
        "TREASURE_DATABASE_URL": "sqlite:///" + (tmp_path / "proxy.sqlite3").as_posix(),
        "TREASURE_DEV_SQLITE": "true",
        "TREASURE_ALLOWED_HOSTS": "video.example.test,localhost,127.0.0.1",
        "TREASURE_PASSKEY_ORIGIN": "https://video.example.test",
        "TREASURE_PASSKEY_RP_ID": "video.example.test",
        "TREASURE_PASSKEYS_ENABLED": "true",
        "TREASURE_COOKIE_SECURE": "true",
        "TREASURE_LOGIN_LIMIT": "20",
    })
    if scenario == "public_http":
        env.update({
            "TREASURE_PASSKEY_ORIGIN": "http://video.example.test",
            "TREASURE_PASSKEYS_ENABLED": "false",
            "TREASURE_COOKIE_SECURE": "false",
        })
    result = subprocess.run([sys.executable, "-c", SCENARIO, scenario], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr or "Isolated reverse-proxy regression failed"
