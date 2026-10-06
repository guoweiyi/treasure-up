"""Synthetic packet timelines across real fMP4 HLS boundaries, not device playback."""
from collections import Counter
from fractions import Fraction
import json
import shutil
import subprocess

import pytest

from app.config import settings
from app.playback import hls
from app.storage.base import file_digest
from app.storage.service import ingest_file, materialize_asset
from test_ingest_runner import db  # noqa: F401
from test_playback_derivatives import archive_fixture


def _run(arguments):
    result = subprocess.run(arguments, capture_output=True, check=True, timeout=60)
    assert not result.stderr, result.stderr.decode(errors='replace')
    return result.stdout


def _audio_packets(path):
    data = json.loads(_run([
        str(settings.ffprobe_path), '-v', 'error', '-protocol_whitelist', 'file,crypto,data',
        '-select_streams', 'a:0', '-show_streams', '-show_packets', '-show_data_hash', 'sha256',
        '-of', 'json', str(path),
    ]))
    assert len(data['streams']) == 1
    scale = Fraction(data['streams'][0]['time_base'])
    packets = data['packets']
    assert len(packets) > 100
    times = [(int(packet['pts']) * scale, int(packet['dts']) * scale,
              int(packet['duration']) * scale if 'duration' in packet else None) for packet in packets]
    return packets, times


@pytest.mark.skipif(not shutil.which(str(settings.ffmpeg_path)) or not shutil.which(str(settings.ffprobe_path)),
                    reason='FFmpeg and FFprobe required for real multi-segment audio timeline checks')
@pytest.mark.parametrize('audio_codec', ['aac', 'eac3'], ids=['aac-stereo', 'ec3-six-channel'])
def test_audio_packet_order_and_relative_timing_survive_multiple_hls_segments(db, tmp_path, audio_codec):
    source = tmp_path / f'{audio_codec}.mp4'
    # Seven seconds with two-second GOPs gives four independently inspectable
    # media segments. Tone encoding is continuous; segmentation is stream copy.
    _run([
        str(settings.ffmpeg_path), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=128x72:rate=25', '-f', 'lavfi', '-i',
        'sine=frequency=997:sample_rate=48000', '-t', '7', '-c:v', 'libx264',
        '-threads', '2', '-g', '50', '-keyint_min', '50', '-sc_threshold', '0',
        '-c:a', audio_codec, '-ac', '2' if audio_codec == 'aac' else '6',
        '-b:a', '192k' if audio_codec == 'aac' else '640k', '-movflags', '+faststart', str(source),
    ])
    source_hash = file_digest(source)
    _, _, archive = archive_fixture(db)
    asset = ingest_file(db, source, kind='media', mime_type='video/mp4')
    archive.asset_id, archive.duration = asset.id, 7
    archive.video_codec, archive.audio_codec = 'h264', audio_codec
    db.flush()
    package = hls.package_variant(db, archive.id, segment_seconds=2)
    index = hls.load_hls_index(db, package.asset_id)
    assert len(index['segments']) >= 3
    delivered = settings.scratch_dir / 'delivered'
    delivered.mkdir()
    materialize_asset(db, index['init_asset_id'], delivered / 'init.mp4')
    lines = ['#EXTM3U', '#EXT-X-VERSION:7', f'#EXT-X-TARGETDURATION:{index["target_duration"]}',
             '#EXT-X-MAP:URI="init.mp4"']
    for number, segment in enumerate(index['segments']):
        filename = f'segment_{number}.m4s'
        materialize_asset(db, segment['asset_id'], delivered / filename)
        lines.extend([f'#EXTINF:{segment["duration"]:.6f},', filename])
    playlist = delivered / 'index.m3u8'
    playlist.write_text('\n'.join([*lines, '#EXT-X-ENDLIST', '']), encoding='utf-8')
    before, source_times = _audio_packets(source)
    after, hls_times = _audio_packets(playlist)

    # Per-packet hashes establish order and identity, not just a concatenated hash.
    assert [packet['data_hash'] for packet in after] == [packet['data_hash'] for packet in before]
    assert all(item[2] is not None and item[2] > 0 for item in source_times), 'Source durations must be measured'
    offset = hls_times[0][0] - source_times[0][0]
    for sequence, (original, packaged) in enumerate(zip(source_times, hls_times)):
        assert packaged[0] - original[0] == offset, f'PTS changed at audio packet {sequence}'
        assert packaged[1] - original[1] == offset, f'DTS changed at audio packet {sequence}'
        if 0 < sequence < len(before) - 1 and packaged[2] is not None:
            assert packaged[2] == original[2], f'Duration changed at interior audio packet {sequence}'
    # FFprobe can omit AAC packet duration for HLS. Its actual next PTS/DTS must
    # still advance by the corresponding measured source duration. This checks
    # continuity across boundaries without inventing a reported HLS duration.
    for sequence in range(1, len(before) - 1):
        source_duration = source_times[sequence][2]
        for timeline in (source_times, hls_times):
            current, following = timeline[sequence], timeline[sequence + 1]
            duration = current[2] if current[2] is not None else source_duration
            assert following[0] == current[0] + duration, f'PTS gap at audio packet {sequence}'
            assert following[1] == current[1] + duration, f'DTS gap at audio packet {sequence}'

    # MP4 edit-list priming (e.g. AAC skip_samples) need not survive fMP4 HLS.
    # Only the track's two edge durations may differ, by at most one measured
    # encoded frame, where FFprobe reports them. Every reported intermediate
    # duration and every timestamp increment was checked exactly above.
    frame_duration = Counter(item[2] for item in source_times[1:-1]).most_common(1)[0][0]
    assert frame_duration > 0
    for edge in (0, -1):
        if hls_times[edge][2] is not None:
            assert hls_times[edge][2] > 0
            assert abs(hls_times[edge][2] - source_times[edge][2]) <= frame_duration
    evidence = {
        'codec': audio_codec, 'segments': len(index['segments']), 'audio_packets': len(before),
        'constant_offset_seconds': str(offset), 'interior_frame_seconds': str(frame_duration),
        'source_first_side_data': before[0].get('side_data_list', []),
        'hls_first_side_data': after[0].get('side_data_list', []),
        'source_missing_duration_packets': sum(item[2] is None for item in source_times),
        'hls_missing_duration_packets': sum(item[2] is None for item in hls_times),
        'hls_interior_duration_comparisons': sum(item[2] is not None for item in hls_times[1:-1]),
        'hls_interior_timestamp_increment_checks': len(before) - 2,
        'source_edge_durations': [str(source_times[i][2]) for i in (0, -1)],
        'hls_edge_durations': [str(hls_times[i][2]) if hls_times[i][2] is not None else None for i in (0, -1)],
    }
    (tmp_path / 'audio-timeline.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print('HLS audio timeline evidence: ' + json.dumps(evidence, sort_keys=True))
    assert file_digest(source) == source_hash
