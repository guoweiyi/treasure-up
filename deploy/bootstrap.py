"""Create local deployment secrets without printing their values."""
import argparse
import base64
import os
from pathlib import Path
import secrets

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=Path(".env"))
args = parser.parse_args()
values = {
    "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
    "TREASURE_ADMIN_USERNAME": "admin",
    "TREASURE_ADMIN_PASSWORD": secrets.token_urlsafe(24),
    "TREASURE_SECRET_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "TREASURE_BACKUP_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "TREASURE_COOKIE_SECURE": "false",
    "TREASURE_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "TREASURE_PORT": "8788",
}
with args.output.open("x", encoding="utf-8") as output:
    for key, value in values.items():
        output.write(f"{key}={value}\n")
try:
    args.output.chmod(0o600)
except OSError:
    pass
print(f"Created {args.output}; protect this file and keep an independent backup key copy.")
