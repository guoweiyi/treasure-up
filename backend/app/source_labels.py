"""Source names stay separate from intentional local display names."""
import re


def source_title(value):
    return value.strip()[:500] if isinstance(value, str) else ""


def is_default_source_title(title, kind, source_id):
    title = source_title(title)
    prefix = "UP" if kind == "creator" else "收藏夹"
    return not title or title == str(source_id) or bool(re.fullmatch(
        re.escape(prefix) + r"\s*" + re.escape(str(source_id)), title, re.IGNORECASE))


def apply_source_title(collection, value):
    """Update automatic names while retaining custom names and latest upstream name."""
    title = source_title(value)
    if not title:
        return
    state = dict(collection.monitor_state or {})
    if (is_default_source_title(collection.title, collection.kind, collection.source_id)
            or collection.title == state.get("source_title")):
        collection.title = title
    state["source_title"] = title
    collection.monitor_state = state


def display_source_title(collection):
    """Also repair the display of pre-existing numeric placeholders without writes."""
    if is_default_source_title(collection.title, collection.kind, collection.source_id):
        return source_title((collection.monitor_state or {}).get("source_title")) or (
            "UP 主资料待获取" if collection.kind == "creator" else "收藏夹名称待获取")
    return collection.title
