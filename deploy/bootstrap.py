"""Create deployment secrets once; only an interactive setup terminal sees its password."""
import argparse
import base64
import ipaddress
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
from urllib.parse import urlsplit

def normalize_origin(origin, *, allow_http=False):
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
    if parsed.scheme == "http" and parsed.hostname != "localhost" and not allow_http:
        raise ValueError("Remote origins require HTTPS; --allow-http explicitly permits an HTTP tunnel without passkeys")
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


def configure_origin(output, origin, *, allow_http=False, stream=None):
    """Explicitly change only site policy in an existing environment file."""
    output, stream = Path(output), stream or sys.stdout
    origin, hostname = normalize_origin(origin, allow_http=allow_http)
    if output.is_symlink() or not stat.S_ISREG(output.stat().st_mode):
        raise ValueError("Site configuration requires a regular environment file, not a symlink")
    original = output.read_bytes()
    text = original.decode("utf-8-sig")
    values = {
        "TREASURE_ALLOWED_HOSTS": ",".join(dict.fromkeys([hostname, "localhost", "127.0.0.1"])),
        "TREASURE_PASSKEY_ORIGIN": origin,
        "TREASURE_PASSKEY_RP_ID": hostname,
        "TREASURE_COOKIE_SECURE": "true" if origin.startswith("https://") else "false",
        "TREASURE_PASSKEYS_ENABLED": "true" if origin.startswith("https://") or hostname == "localhost" else "false",
    }
    newline = "\r\n" if "\r\n" in text else "\n"
    lines, seen = [], set()
    for line in text.splitlines(keepends=True):
        assignment = re.match(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)", line)
        if assignment:
            key, value = assignment.groups()
            stripped = value.lstrip()
            # Refuse ambiguous multiline dotenv values rather than accidentally
            # editing a line inside a secret. Generated files use single lines.
            if stripped.startswith(("'", '"')):
                quote, escaped, closed = stripped[0], False, False
                for character in stripped[1:]:
                    if character == quote and not escaped:
                        closed = True
                        break
                    escaped = character == "\\" and not escaped
                if not closed:
                    raise ValueError("Multiline environment values require manual site configuration")
            if key in values:
                if key in seen:
                    raise ValueError(f"Duplicate site setting: {key}; resolve it before changing the origin")
                seen.add(key)
                ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                line = f"{key}={values[key]}{ending}"
        lines.append(line)
    content = "".join(lines)
    missing = [key for key in values if key not in seen]
    if missing and content and not content.endswith("\n"):
        content += newline
    content += "".join(f"{key}={values[key]}{newline}" for key in missing)
    updated = (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + content.encode("utf-8")
    if updated == original:
        print(f"Site already configured: {origin}", file=stream)
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=".env.treasure-site-", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(updated)
            destination.flush()
            os.fsync(destination.fileno())
        if output.is_symlink() or output.read_bytes() != original:
            raise ValueError("Environment changed during update; retry without concurrent edits")
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Site configured: {origin}. Passwords, encryption keys and other settings were preserved.", file=stream)
    print("The allowed hosts are this hostname, localhost and 127.0.0.1. Recreate the API container to apply the settings.", file=stream)
    if origin.startswith("http://") and hostname != "localhost":
        print("HTTP tunnel: password sessions are not encrypted in transit; passkeys are disabled. Use HTTPS for a permanent deployment.", file=stream)
    return True


def create_environment(output, *, origin="http://localhost:8788", port=8788, admin_username="admin", allow_http=False, stream=None):
    """Return whether a new file was created. Never load or replace an existing file."""
    output, stream = Path(output), stream or sys.stdout
    if output.exists() or output.is_symlink():
        print(f"Existing {output} kept; passwords and settings were not changed.", file=stream)
        return False
    origin, hostname = normalize_origin(origin, allow_http=allow_http)
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
        "TREASURE_PASSKEYS_ENABLED": "true" if origin.startswith("https://") or hostname == "localhost" else "false",
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
    parser.add_argument("--origin", help="Public HTTPS origin; explicitly updates site settings in an existing file")
    parser.add_argument("--allow-http", action="store_true", help="Explicitly allow a public HTTP tunnel; disables passkeys and Secure cookies")
    parser.add_argument("--admin-username", default="admin")
    args = parser.parse_args()
    try:
        created = create_environment(args.output, origin=args.origin or f"http://localhost:{args.port}", port=args.port,
                                     admin_username=args.admin_username, allow_http=args.allow_http)
        if not created and args.origin:
            configure_origin(args.output, args.origin, allow_http=args.allow_http)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
