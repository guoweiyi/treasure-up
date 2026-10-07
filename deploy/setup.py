"""Create deployment secrets once, or update the site's public address without changing secrets."""
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


def local_hostname(hostname):
    if hostname == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or any(address in ipaddress.ip_network(network) for network in
                                      ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def passkeys_available(origin, hostname):
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return origin.startswith("https://") or hostname == "localhost"
    return False


def normalize_origin(origin, *, allow_http=False):
    if not isinstance(origin, str) or any(character.isspace() or ord(character) < 32 for character in origin):
        raise ValueError("Origin must not contain whitespace or control characters")
    parsed = urlsplit(origin.rstrip("/"))
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None
        or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Origin must be a complete HTTPS origin, or an HTTP localhost/LAN address for local use")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    else:
        if address.version != 4:
            raise ValueError("For an IPv6 deployment, use a DNS hostname as the public origin")
        if address.is_unspecified or address.is_multicast:
            raise ValueError("Origin must be a browser access address, not a wildcard or multicast address")
    if parsed.scheme == "http" and not local_hostname(parsed.hostname) and not allow_http:
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


def normalize_bind_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("Bind address must be an IPv4 address, such as 127.0.0.1 or 0.0.0.0") from error
    if address.version != 4 or address.is_multicast:
        raise ValueError("Bind address must be a unicast IPv4 address, or 0.0.0.0 for all interfaces")
    return str(address)


def configure_site(output, *, origin=None, bind_address=None, allow_http=False, stream=None):
    """Explicitly change only site policy in an existing environment file."""
    output, stream = Path(output), stream or sys.stdout
    values = {}
    if origin is not None:
        origin, hostname = normalize_origin(origin, allow_http=allow_http)
        values.update({
            "TREASURE_ALLOWED_HOSTS": ",".join(dict.fromkeys([hostname, "localhost", "127.0.0.1"])),
            "TREASURE_PASSKEY_ORIGIN": origin,
            "TREASURE_PASSKEY_RP_ID": hostname,
            "TREASURE_COOKIE_SECURE": "true" if origin.startswith("https://") else "false",
            "TREASURE_PASSKEYS_ENABLED": "true" if passkeys_available(origin, hostname) else "false",
        })
    if bind_address is not None:
        values["TREASURE_BIND_ADDRESS"] = normalize_bind_address(bind_address)
    if not values:
        return False
    if output.is_symlink() or not stat.S_ISREG(output.stat().st_mode):
        raise ValueError("Site configuration requires a regular environment file, not a symlink")
    original = output.read_bytes()
    text = original.decode("utf-8-sig")
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
        print("Site settings are already configured; nothing changed.", file=stream)
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
    print("Site settings updated. Passwords, encryption keys and other settings were preserved.", file=stream)
    if origin is not None:
        print(f"Site: {origin}. Allowed hosts: {values['TREASURE_ALLOWED_HOSTS']}", file=stream)
        if not passkeys_available(origin, hostname):
            print("Password login is enabled. Passkeys need localhost or an HTTPS DNS hostname.", file=stream)
    if bind_address is not None:
        print(f"Listening address: {values['TREASURE_BIND_ADDRESS']}", file=stream)
    print("Run docker compose up -d --wait with your usual Compose files to apply these settings.", file=stream)
    return True


def configure_origin(output, origin, *, allow_http=False, stream=None):
    """Compatibility entry point for existing deployment scripts."""
    return configure_site(output, origin=origin, allow_http=allow_http, stream=stream)


def create_environment(output, *, origin="http://localhost:8788", port=8788, admin_username="admin", bind_address="127.0.0.1", allow_http=False, stream=None):
    """Return whether a new file was created. Never load or replace an existing file."""
    output, stream = Path(output), stream or sys.stdout
    if output.exists() or output.is_symlink():
        print(f"Existing {output} kept; passwords and settings were not changed.", file=stream)
        print("Use --show-login in a private terminal to view the saved initial login, or --origin to change the public address.", file=stream)
        return False
    origin, hostname = normalize_origin(origin, allow_http=allow_http)
    bind_address = normalize_bind_address(bind_address)
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
        "TREASURE_BIND_ADDRESS": bind_address,
        "TREASURE_PASSKEY_RP_ID": hostname,
        "TREASURE_PASSKEY_ORIGIN": origin,
        "TREASURE_PASSKEY_RP_NAME": "Treasure Up",
        "TREASURE_PASSKEYS_ENABLED": "true" if passkeys_available(origin, hostname) else "false",
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
    if not passkeys_available(origin, hostname):
        print("Password login is enabled. Passkeys need localhost or an HTTPS DNS hostname.", file=stream)
    if stream.isatty():
        print(f"Initial administrator: {admin_username}", file=stream)
        print(f"Initial password: {values['TREASURE_ADMIN_PASSWORD']}", file=stream)
        print("Change this password after signing in. It is not written to application logs.", file=stream)
    else:
        print("Initial password was not printed to redirected output. Read it locally from the protected environment file.", file=stream)
    return True


def show_login(output, *, stream=None):
    """Read only the saved setup login; never put it in pipes or CI logs."""
    output, stream = Path(output), stream or sys.stdout
    if not stream.isatty():
        raise ValueError("Login details can only be shown in an interactive terminal; do not redirect this command")
    if output.is_symlink() or not output.is_file() or output.stat().st_size > 65536:
        raise ValueError("A regular environment file is required; run setup first")
    values = {}
    for line in output.read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?(TREASURE_ADMIN_USERNAME|TREASURE_ADMIN_PASSWORD)\s*=(.*)", line)
        if not match:
            continue
        key, value = match.groups()
        if key in values:
            raise ValueError("Duplicate administrator settings; review the environment file locally")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0] or "\\" in value:
                raise ValueError("Complex administrator settings must be read directly from the environment file")
            value = value[1:-1]
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Administrator settings contain unsupported characters")
        values[key] = value
    if not values.get("TREASURE_ADMIN_PASSWORD"):
        raise ValueError("No initial password is saved in this file; use your current password or the account recovery procedure")
    print(f"Initial administrator: {values.get('TREASURE_ADMIN_USERNAME', 'admin')}", file=stream)
    print(f"Initial password: {values['TREASURE_ADMIN_PASSWORD']}", file=stream)
    print("This is the saved first-run password. It does not reset a password changed after installation.", file=stream)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".env"))
    parser.add_argument("--port", type=int, default=8788, help="Published web port for a new installation; an existing .env keeps its port")
    parser.add_argument("--origin", help="Public HTTPS origin; explicitly updates site settings in an existing file")
    parser.add_argument("--bind-address", help="127.0.0.1 (default), 0.0.0.0 for NAS/LAN access, or a specific IPv4 interface; updates existing files explicitly")
    parser.add_argument("--allow-http", action="store_true", help="Explicitly allow a public HTTP tunnel; disables passkeys and Secure cookies")
    parser.add_argument("--admin-username", default="admin", help="Administrator name for a new database; never renames an existing account")
    parser.add_argument("--show-login", action="store_true", help="Show the saved initial login only in a private interactive terminal; never reset it")
    args = parser.parse_args()
    try:
        if args.show_login:
            if args.origin or args.bind_address:
                parser.error("--show-login and site configuration are separate operations")
            show_login(args.output)
            return
        created = create_environment(args.output, origin=args.origin or f"http://localhost:{args.port}", port=args.port,
                                     admin_username=args.admin_username, bind_address=args.bind_address or "127.0.0.1", allow_http=args.allow_http)
        if not created and (args.origin or args.bind_address):
            configure_site(args.output, origin=args.origin, bind_address=args.bind_address, allow_http=args.allow_http)
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
