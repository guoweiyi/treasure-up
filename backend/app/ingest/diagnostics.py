"""Useful error locations without exception messages, query parameters or locals."""
import hashlib
import json
from pathlib import Path
import re


_APP = Path(__file__).resolve().parents[1]
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")


def summarize(error):
    kind = type(error).__name__
    kind = kind if _IDENTIFIER.fullmatch(kind) else "Exception"
    frames = []
    traceback = error.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        try:
            path = Path(code.co_filename).resolve().relative_to(_APP)
        except (ValueError, OSError):
            pass
        else:
            # Only trusted application code locations are useful to operators.
            if path.suffix == ".py" and all(_IDENTIFIER.fullmatch(part) for part in path.with_suffix("").parts):
                function = code.co_name
                frames.append({"module": "app." + ".".join(path.with_suffix("").parts),
                               "function": function if _IDENTIFIER.fullmatch(function) else "unknown",
                               "line": traceback.tb_lineno})
                frames = frames[-8:]
        traceback = traceback.tb_next
    details = {"type": kind, "frames": frames}
    details["fingerprint"] = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()[:16]
    return details
