"""Merge measurements only within one selected media rendition and its stream-copy HLS."""
import math


_MEASUREMENTS = frozenset({"fps", "video_bitrate_bps", "audio_bitrate_bps", "total_bitrate_bps",
                           "size_bytes", "duration_seconds"})
_RATE_EVIDENCE = ("total_bitrate_basis", "total_bitrate_estimated", "total_bitrate_includes_container")


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def merge_media_properties(*sources):
    """Absent probe fields must not erase prior measurements of the same bytes.

    Other metadata still follows normal precedence, including false Dolby flags,
    empty codec evidence and explicit nulls. Callers must select the source first.
    """
    result = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        previous = result
        result = {**result, **source}
        for field in _MEASUREMENTS:
            if _positive(previous.get(field)) and not _positive(source.get(field)):
                result[field] = previous[field]
        if "total_bitrate_bps" in source:
            if _positive(source["total_bitrate_bps"]):
                # A replacement measurement owns its evidence. An earlier
                # estimate must not label a later measured rate as estimated.
                for field in _RATE_EVIDENCE:
                    if field not in source:
                        result.pop(field, None)
            elif _positive(previous.get("total_bitrate_bps")):
                for field in _RATE_EVIDENCE:
                    if field in previous:
                        result[field] = previous[field]
                    else:
                        result.pop(field, None)
    return result


def with_source_file_average(media, *, size, duration):
    """Bounded metadata fallback; never probe a file in a playback request.

    File size / source duration is an average including mux overhead, not a
    measured elementary video/audio rate. Do not infer either track's bitrate.
    """
    result = dict(media)
    if _positive(result.get("duration_seconds")):
        duration = result["duration_seconds"]
    if not _positive(result.get("total_bitrate_bps")) and _positive(size) and _positive(duration):
        result.update(total_bitrate_bps=size * 8 / duration, total_bitrate_basis="file_size_over_duration",
                      total_bitrate_estimated=True, total_bitrate_includes_container=True)
    return result
