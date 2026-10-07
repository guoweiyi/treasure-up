"""Smoke-check an isolated local installation without printing credentials or calling Bilibili."""
import argparse
from http.cookiejar import CookieJar
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


class SmokeFailure(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def run_check(url, env_file):
    origin = urllib.parse.urlsplit(url)
    if (origin.scheme not in {"http", "https"} or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
            or origin.username or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}):
        raise SmokeFailure("Use an isolated loopback server URL")
    credentials = {}
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"TREASURE_ADMIN_USERNAME", "TREASURE_ADMIN_PASSWORD", "TREASURE_PASSKEY_ORIGIN"}:
            credentials[key] = value.strip().strip('"').strip("'")
    username = credentials.get("TREASURE_ADMIN_USERNAME", "admin")
    password = credentials.get("TREASURE_ADMIN_PASSWORD")
    if not password:
        raise SmokeFailure("The isolated installation needs its initial admin password in the env file")
    request_origin = credentials.get("TREASURE_PASSKEY_ORIGIN", url).rstrip("/")
    cookies = CookieJar()
    browser = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                          urllib.request.HTTPCookieProcessor(cookies), NoRedirect())
    csrf = ""

    def request(path, body=None, expected=200):
        headers = {"Accept": "application/json"}
        if body is not None:
            headers.update({"Content-Type": "application/json", "Origin": request_origin, "X-CSRF-Token": csrf})
        req = urllib.request.Request(url.rstrip("/") + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            response = browser.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        except urllib.error.URLError:
            raise SmokeFailure(f"Cannot reach local service: {path}") from None
        with response:
            if response.status != expected:
                raise SmokeFailure(f"Unexpected HTTP {response.status}: {path}")
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise SmokeFailure(f"Unexpectedly large response: {path}")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise SmokeFailure(f"Invalid JSON response: {path}") from None

    if request("/health").get("status") != "ok" or request("/api/v1/server").get("application") != "treasure-up":
        raise SmokeFailure("Application health or identity mismatch")
    request("/api/v1/auth/me", expected=401)
    login = request("/api/v1/auth/login", {"username": username, "password": password})
    csrf = login.get("csrf_token", "")
    if not csrf or login.get("user", {}).get("role") != "admin" or login["user"].get("username") != username:
        raise SmokeFailure("Administrator initialization did not succeed")
    try:
        if request("/api/v1/auth/me").get("user") != login["user"]:
            raise SmokeFailure("The login session was not retained")
        catalog = request("/api/v1/videos?view=card&page_size=1")
        if not isinstance(catalog.get("items"), list) or not isinstance(catalog.get("total"), int):
            raise SmokeFailure("The catalog is not readable")
        request("/api/v1/admin/accounts")
    finally:
        request("/api/v1/auth/logout", {})
    request("/api/v1/auth/me", expected=401)
    return login["user"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8788")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        run_check(args.url, args.env_file)
    except SmokeFailure as error:
        parser.exit(1, f"Local installation smoke check failed: {error}\n")
    except (ValueError, OSError, KeyError, AttributeError, TypeError):
        parser.exit(1, "Local installation smoke check failed: invalid configuration or response.\n")
    print("Local health, initialization, login, catalog, administration and logout checks passed.")
