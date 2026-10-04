"""Create deployment secrets once; only an interactive setup terminal sees its password."""
import argparse
import base64
import ipaddress
import os
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlsplit

def normalize_origin(origin):
    if not isinstance(origin, str) or any(character.isspace() or ord(character) < 32 for character in origin):
        raise ValueError("Origin must not contain whitespace or control characters")
    parsed = urlsplit(origin.rstrip("/"))
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None
        or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Origin must be a complete HTTPS origin, or http://localhost for local use")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Use localhost or a DNS hostname for passkeys, not an IP address")
    if parsed.scheme == "http" and parsed.hostname != "localhost":
        raise ValueError("Remote origins require HTTPS")
    hostname = parsed.hostname.encode("idna").decode("ascii")
    if len(hostname) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in hostname.split(".")):
        raise ValueError("Origin requires a valid DNS hostname")
    port_number = parsed.port
    if port_number == 0 or parsed.netloc.endswith(":"):
        raise ValueError("Origin requires a valid port between 1 and 65535")
    # Browser Origin/WebAuthn origin serialization removes default ports.
    default_port = 443 if parsed.scheme == "https" else 80
    port = f":{port_number}" if port_number is not None and port_number != default_port else ""
    return f"{parsed.scheme}://{hostname}{port}", hostname


def create_environment(output, *, origin="http://localhost:8788", port=8788, admin_username="admin", stream=None):
    """Return whether a new file was created. Never load or replace an existing file."""
    output, stream = Path(output), stream or sys.stdout
    if output.exists() or output.is_symlink():
        print(f"Existing {output} kept; passwords and settings were not changed.", file=stream)
        return False
    origin, hostname = normalize_origin(origin)
    if not 1 <= port <= 65535 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", admin_username):
        raise ValueError("Invalid port or administrator username")
    values = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "TREASURE_ADMIN_USERNAME": admin_username,
        "TREASURE_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "TREASURE_SECRET_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "TREASURE_BACKUP_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "TREASURE_COOKIE_SECURE": "true" if origin.startswith("https://") else "false",
        "TREASURE_ALLOWED_HOSTS": ",".join(dict.fromkeys([hostname, "localhost", "127.0.0.1"])),
        "TREASURE_PORT": str(port),
        "TREASURE_PASSKEY_RP_ID": hostname,
        "TREASURE_PASSKEY_ORIGIN": origin,
        "TREASURE_PASSKEY_RP_NAME": "Treasure Up",
    }
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"Existing {output} kept; passwords and settings were not changed.", file=stream)
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
        destination.write("".join(f"{key}={value}\n" for key, value in values.items()))
        destination.flush()
        os.fsync(destination.fileno())
    print(f"Created {output}. Keep it private; keep the backup key independently.", file=stream)
    print(f"Site: {origin}", file=stream)
    if stream.isatty():
        print(f"Initial administrator: {admin_username}", file=stream)
        print(f"Initial password (shown only during this creation): {values['TREASURE_ADMIN_PASSWORD']}", file=stream)
    else:
        print("Initial password was not printed to redirected output. Read it locally from the protected environment file.", file=stream)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".env"))
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--origin", help="Public HTTPS origin; defaults to http://localhost:<port>")
    parser.add_argument("--admin-username", default="admin")
    args = parser.parse_args()
    try:
        create_environment(args.output, origin=args.origin or f"http://localhost:{args.port}", port=args.port,
                           admin_username=args.admin_username)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
