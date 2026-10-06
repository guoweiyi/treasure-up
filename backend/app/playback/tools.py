from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from app.config import settings
from app.storage.base import StorageError


class PlaybackError(StorageError):
    pass


MAX_TOOL_OUTPUT = 8 * 1024**2


def run_tool(arguments, *, check_active=None, timeout=7200, capture=False, reject_errors=False):
    """Poll cancellation; optionally reject any diagnostic from a -v error command.

    Some FFmpeg versions return success after decoder errors, even with -xerror.
    Callers using stderr for measurements must leave this option disabled.
    Raw diagnostics are never included in public exceptions.
    """
    guard = check_active or (lambda: None)
    guard()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL,
                stdout=output if capture else subprocess.DEVNULL, stderr=errors)
        except OSError:
            raise PlaybackError("Media processing tool is unavailable") from None
        started = time.monotonic()
        def check_output_limit():
            if any(os.fstat(stream.fileno()).st_size > MAX_TOOL_OUTPUT for stream in (output, errors)):
                raise PlaybackError("Media processing diagnostic output limit exceeded")
        try:
            while process.poll() is None:
                guard()
                check_output_limit()
                if time.monotonic() - started > timeout:
                    raise PlaybackError("Media processing time limit exceeded")
                time.sleep(0.2)
            guard()
            check_output_limit()
            if process.returncode:
                raise PlaybackError("Media processing failed; original asset remains unchanged")
            if reject_errors and os.fstat(errors.fileno()).st_size:
                raise PlaybackError("Media processing reported errors; original asset remains unchanged")
            output.seek(0)
            errors.seek(0)
            return output.read(MAX_TOOL_OUTPUT), errors.read(MAX_TOOL_OUTPUT)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def probe_media(path: Path, *, check_active=None, extended=False):
    # A large first video fragment can exhaust FFprobe's default 5 MB before
    # dependent E-AC-3 frames arrive. Retry only where the caller needs it.
    budget = ["-probesize", "33554432", "-analyzeduration", "10000000"] if extended else []
    raw, _ = run_tool([str(settings.ffprobe_path), "-v", "error", "-protocol_whitelist", "file,crypto,data", *budget,
        "-show_streams", "-show_format", "-of", "json", str(path)],
        check_active=check_active, timeout=120, capture=True, reject_errors=True)
    try:
        result = json.loads(raw)
        if not isinstance(result.get("streams"), list):
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise PlaybackError("Invalid media inspection result") from None
