"""One policy for catalog reads and every authenticated media transport."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Setting
from app.security import authenticated


def allow_guest_access(db):
    row = db.get(Setting, "display")
    # Fail closed for unset or malformed historic configuration.
    return bool(row and isinstance(row.value, dict) and row.value.get("allow_guest_access") is True)


def enforce_library_access(request: Request, db: Session = Depends(get_db)):
    path = request.url.path.rstrip("/")
    root = path.removeprefix("/api/v1/").split("/", 1)[0]
    protected = root in {"videos", "creators", "collections", "playlists", "parts", "assets", "playback-sessions"}
    if path == "/api/v1/library/stats":
        protected = True
    if protected and not allow_guest_access(db):
        try:
            authenticated(request, db)
        except HTTPException as error:
            if error.status_code == 401:
                raise HTTPException(401, "请登录后浏览媒体库") from None
            raise
