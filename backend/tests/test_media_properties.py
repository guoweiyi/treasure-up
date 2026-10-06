import pytest

from app.media_properties import merge_media_properties, with_source_file_average


def test_missing_measurements_do_not_erase_same_source_values_or_change_flags():
    original = {"fps": 59.94, "video_bitrate_bps": 8_000_000, "audio_bitrate_bps": 192_000,
                "total_bitrate_bps": 8_200_000, "total_bitrate_basis": "file_size_over_duration",
                "dolby_atmos": True, "ec3": {"joc": True}, "note": "old"}
    merged = merge_media_properties(original, {"fps": None, "video_bitrate_bps": 0,
        "audio_bitrate_bps": None, "total_bitrate_bps": None, "total_bitrate_basis": None,
        "dolby_atmos": False, "ec3": {}, "note": None})
    assert {field: merged[field] for field in ("fps", "video_bitrate_bps", "audio_bitrate_bps", "total_bitrate_bps", "total_bitrate_basis")} == {
        field: original[field] for field in ("fps", "video_bitrate_bps", "audio_bitrate_bps", "total_bitrate_bps", "total_bitrate_basis")}
    assert merged["dolby_atmos"] is False and merged["ec3"] == {} and merged["note"] is None
    assert merge_media_properties(original, {"fps": 60, "audio_bitrate_bps": 256_000})["fps"] == 60
    assert original["dolby_atmos"] is True


def test_file_average_never_infers_track_bitrates_or_overwrites_a_measurement():
    result = with_source_file_average({"fps": None, "video_bitrate_bps": None}, size=1000, duration=4)
    assert result["total_bitrate_bps"] == 2000
    assert result["total_bitrate_basis"] == "file_size_over_duration"
    assert result["total_bitrate_estimated"] and result["total_bitrate_includes_container"]
    assert result["fps"] is None and result["video_bitrate_bps"] is None
    assert "audio_bitrate_bps" not in result
    known = {"total_bitrate_bps": 1200, "total_bitrate_basis": "measured"}
    assert with_source_file_average(known, size=1000, duration=4) == known
    assert with_source_file_average({"duration_seconds": 2}, size=1000, duration=4)["total_bitrate_bps"] == 4000
    assert with_source_file_average({"duration_seconds": -2}, size=1000, duration=4)["total_bitrate_bps"] == 2000


def test_new_rate_replaces_older_estimate_evidence():
    estimate = with_source_file_average({}, size=1000, duration=4)
    measured = merge_media_properties(estimate, {"total_bitrate_bps": 1800})
    assert measured == {"total_bitrate_bps": 1800}
    labelled = merge_media_properties(estimate, {"total_bitrate_bps": 1800, "total_bitrate_basis": "new_measurement"})
    assert labelled == {"total_bitrate_bps": 1800, "total_bitrate_basis": "new_measurement"}


@pytest.mark.parametrize("size,duration", [(0, 4), (1000, 0), (1000, None), (1000, float('nan')), (1000, True)])
def test_file_average_requires_valid_metadata(size, duration):
    assert "total_bitrate_bps" not in with_source_file_average({}, size=size, duration=duration)
