"""Exercise the checked-in proxy rules in a disposable local nginx container.

Opt in with TREASURE_RUN_NGINX_TESTS=1. Uses an already built web image and a
loopback-only random port; does not start, rebuild or modify application services.
"""
import json
import gzip
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest


DEPLOY = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("TREASURE_RUN_NGINX_TESTS") != "1",
    reason="Explicit TREASURE_RUN_NGINX_TESTS=1 and a local Docker web image required",
)


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", timeout=30).stdout.strip()


def request(base, path, method="GET", headers=None):
    try:
        with urlopen(Request(base + path, method=method, headers=headers or {}), timeout=3) as response:
            return response.status, response.headers
    except HTTPError as error:
        error.close()
        return error.code, error.headers


@pytest.fixture
def proxy(tmp_path):
    name = "treasure-security-nginx-" + uuid4().hex[:12]
    # Only substitute service discovery. All request rules come from production.
    config = (DEPLOY / "nginx.conf").read_text(encoding="utf-8")
    config = config.replace("server api:8000 resolve;", "server 127.0.0.1:8000;")
    config += ('\nserver { listen 8000; location = /api/v1/parts/slow/danmaku { '
               'limit_rate 1k; alias /fixture/slow-body; '
               '} location / { return 200 "fixture"; } }\n')
    # pytest makes tmp_path 0700 on Linux. Only mount this synthetic public
    # fixture directory so nginx's unprivileged worker can traverse it.
    public_fixture = tmp_path / "fixture"
    public_fixture.mkdir()
    public_fixture.chmod(0o755)
    slow_body = public_fixture / "slow-body"
    slow_body.write_bytes(b"x" * 65536)
    slow_body.chmod(0o644)
    assets = tmp_path / "site" / "assets"
    assets.mkdir(parents=True)
    bundle = b"const publicBundle = 'static code only';\n" * 1000
    (assets / "fixture-hash.js").write_bytes(bundle)
    (assets / "fixture-hash.js.gz").write_bytes(gzip.compress(bundle))
    fixture = tmp_path / "default.conf"
    fixture.write_text(config, encoding="utf-8")
    try:
        docker("run", "--rm", "-d", "--pull=never", "--name", name,
               "-p", "127.0.0.1::80",
               "--mount", f"type=bind,source={fixture},target=/etc/nginx/conf.d/default.conf,readonly",
               "--mount", f"type=bind,source={public_fixture},target=/fixture,readonly",
               "--mount", f"type=bind,source={tmp_path / 'site'},target=/usr/share/nginx/html,readonly",
               "--mount", f"type=bind,source={DEPLOY / 'security-headers.conf'},target=/etc/nginx/security-headers.conf,readonly",
               os.environ.get("TREASURE_NGINX_TEST_IMAGE", "treasure-up-web:0.3.2"))
        published = json.loads(docker("inspect", "--format", '{{json .NetworkSettings.Ports}}', name))
        base = "http://127.0.0.1:" + published["80/tcp"][0]["HostPort"]
        for _ in range(50):
            try:
                if request(base, "/health")[0] == 200:
                    break
            except (URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(0.1)
        else:
            pytest.fail("Isolated nginx did not become ready")
        yield base
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


def test_precompressed_hashed_bundle_negotiation_and_security_headers(proxy):
    path = proxy + '/assets/fixture-hash.js'
    with urlopen(Request(path, headers={'Accept-Encoding': 'gzip'}), timeout=5) as response:
        packed = response.read()
        assert response.headers['Content-Encoding'] == 'gzip'
        assert 'Accept-Encoding' in response.headers['Vary']
        assert 'immutable' in response.headers['Cache-Control']
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
        original = gzip.decompress(packed)
    with urlopen(Request(path, headers={'Accept-Encoding': 'identity'}), timeout=5) as response:
        assert response.headers.get('Content-Encoding') is None
        assert response.read() == original
    assert len(packed) < len(original) / 10
    assert request(proxy, '/assets/missing.js')[0] == 404
    assert request(proxy, '/protected-media/hidden')[0] == 404


@pytest.mark.parametrize("kind", ["danmaku", "playback"])
def test_flood_rejected_across_path_variants_without_blocking_other_routes(proxy, kind):
    path = "/api/v1/parts/fixture/danmaku" if kind == "danmaku" else "/api/v1/playback-sessions"
    method = "GET" if kind == "danmaku" else "POST"
    # Warm up nginx workers before the burst. Query strings, part IDs, cookies,
    # raw forwarding headers and a trailing slash must not grant a fresh bucket.
    assert request(proxy, path, method)[0] == 200
    results = []
    for index in range(50):
        target = path.replace("fixture", f"part-{index}")
        target += ("/" if index % 2 else "") + f"?nonce={index}"
        results.append(request(proxy, target, method, {
            "X-Forwarded-For": f"192.0.2.{index + 1}",
            "Cookie": f"treasure_viewer=changed-{index}",
        })[0])
    assert 429 in results
    for suffix in ("", "/"):
        status, headers = request(proxy, path + suffix, method)
        assert status == 429
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    for other in ("/health", "/api/v1/videos", "/api/v1/playback-sessions/session/assets/asset"):
        assert request(proxy, other)[0] == 200


def test_slow_danmaku_responses_have_a_concurrency_ceiling(proxy):
    release = threading.Event()
    ready = [threading.Event() for _ in range(4)]

    def hold(index):
        with urlopen(proxy + "/api/v1/parts/slow/danmaku", timeout=5) as response:
            assert response.status == 200
            ready[index].set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(hold, index) for index in range(4)]
        try:
            if not all(event.wait(5) for event in ready):
                # Report an HTTP/setup error instead of hiding it behind a
                # readiness timeout from the worker thread.
                for future in futures:
                    if future.done():
                        future.result()
                pytest.fail("Slow fixture responses did not become ready")
            assert request(proxy, "/api/v1/parts/another/danmaku")[0] == 429
            assert request(proxy, "/api/v1/videos")[0] == 200
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
