"""Load the configuration files mounted for this process, then execute its command."""
import json
import os
from pathlib import Path
import re
import stat
import sys


def read_configuration(filename):
    path = Path(filename)
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Configuration requires an absolute path without symbolic links")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536:
            raise ValueError("Configuration requires a regular file no larger than 64 KiB")
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError("Configuration file exceeds 64 KiB")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Configuration contains duplicate keys")
            result[key] = value
        return result

    try:
        values = json.loads(content.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Configuration file is not valid UTF-8 JSON") from None
    if not isinstance(values, dict) or any(
        not re.fullmatch(r"TREASURE_[A-Z0-9_]+", key) or not isinstance(value, str) or "\x00" in value
        for key, value in values.items()
    ):
        raise ValueError("Configuration must contain only TREASURE_* keys with string values")
    return values


def main():
    try:
        files = [os.environ.get(key) for key in ("TREASURE_CONFIG_FILE", "TREASURE_ROLE_CONFIG_FILE")]
        values = {}
        for filename in files:
            if filename:
                values.update(read_configuration(filename))
        for key, value in values.items():
            os.environ.setdefault(key, value)
        if len(sys.argv) < 2:
            raise ValueError("No application command was provided")
        os.execvp(sys.argv[1], sys.argv[1:])
    except (ValueError, OSError):
        # Errors must not include parsed values or credentials from malformed files.
        print("Application configuration or command is invalid; check the mounted configuration and service command.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
