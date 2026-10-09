"""Creator membership in Bilibili listings and authoritative video details."""

from .errors import IngestError


def listing_creator_role(row, uid):
    # Joint submissions appear in every participant's space, while mid remains
    # the submitting owner's UID. The flag is a listing hint, not permission
    # to capture a video: capture and the worker verify the detail's staff.
    if row.get("mid") is None or str(row["mid"]) == str(uid):
        return "owner"
    if row.get("is_union_video") is True or type(row.get("is_union_video")) is int and row["is_union_video"] == 1:
        return "staff"
    raise IngestError("UP 投稿作者不匹配", code="invalid_response")


def video_has_creator(data, uid):
    """Only owner/staff identities from video details establish membership."""
    if not isinstance(data, dict):
        return False
    owner = data.get("owner")
    if isinstance(owner, dict) and str(owner.get("mid")) == str(uid):
        return True
    staff = data.get("staff")
    return isinstance(staff, list) and any(
        isinstance(member, dict) and str(member.get("mid")) == str(uid)
        for member in staff
    )
