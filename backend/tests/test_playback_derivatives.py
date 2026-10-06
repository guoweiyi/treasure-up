"""Optional SDR output and lossless HLS retain independent, honest outcomes."""
from pathlib import Path
import os

import pytest
from sqlalchemy import select

from app.config import settings
from app.ingest import media
from app.models import MediaVariant, Video, VideoPart
from app.playback import hls, tools
from app.storage.service import ingest_file
from app.storage.base import file_digest
from test_ingest_runner import db  # noqa: F401


def archive_fixture(db):
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    path = settings.scratch_dir / 'archive.mp4'
    path.write_bytes(b'archive fixture')
    asset = ingest_file(db, path, kind='media', mime_type='video/mp4')
    video = Video(bvid='BV124YF68Ekd', capture_status='complete', metadata_json={'ingest_state': {'media': 'complete'}})
    db.add(video); db.flush()
    part = VideoPart(video_id=video.id, cid='fixture', duration=600)
    db.add(part); db.flush()
    archive = MediaVariant(part_id=part.id, asset_id=asset.id, kind='archive', format_key='fixture', duration=600)
    db.add(archive); db.commit()
    return video, part, archive


def test_extended_probe_has_fixed_budget_and_existing_timeout(monkeypatch):
    commands = []
    def tool(args, **kwargs):
        commands.append((args, kwargs))
        return b'{"streams":[]}', b''
    monkeypatch.setattr(tools, 'run_tool', tool)
    tools.probe_media(Path('local.m3u8'), extended=True)
    args, kwargs = commands[0]
    assert args[args.index('-probesize') + 1] == '33554432'
    assert args[args.index('-analyzeduration') + 1] == '10000000'
    assert kwargs['timeout'] == 120


@pytest.mark.parametrize('problem', ['none', 'channels', 'packets'])
def test_hls_extended_probe_does_not_accept_changed_channels_or_audio_packets(db, monkeypatch, problem):
    _, _, archive = archive_fixture(db)
    video = {'codec_type': 'video', 'index': 0, 'codec_name': 'hevc', 'width': 3840, 'height': 2160,
             'avg_frame_rate': '60000/1001', 'bit_rate': '8000000'}
    audio = {'codec_type': 'audio', 'index': 1, 'codec_name': 'eac3', 'channels': 8, 'channel_layout': '7.1',
             'sample_rate': '48000', 'bit_rate': '1024000'}
    calls = []
    def probe(path, **kwargs):
        calls.append((path.name, kwargs.get('extended', False)))
        stream = dict(audio)
        if path.suffix == '.m3u8' and (not kwargs.get('extended') or problem == 'channels'):
            stream.update(channels=6, channel_layout='5.1(side)')
        return {'streams': [video, stream], 'format': {'duration': 600}}
    def mux(args, **kwargs):
        folder = Path(args[-1]).parent
        (folder/'init.mp4').write_bytes(b'init')
        (folder/'segment_000000.m4s').write_bytes(b'segment')
        (folder/'index.m3u8').write_text('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:600,\nsegment_000000.m4s\n#EXT-X-ENDLIST\n')
    ec3 = {'joc': True, 'complexity_index_type_a': 16, 'substreams': [[0, 6, 0, 0, 7, 1, 1, 16]]}
    monkeypatch.setattr(hls, 'probe_media', probe)
    monkeypatch.setattr(hls, 'run_tool', mux)
    # This test isolates the post-mux preservation gates; real fragment layout
    # and timestamps are covered by the FFmpeg integration tests.
    monkeypatch.setattr(hls, 'fragment_movie_timescale', lambda _: 48000)
    monkeypatch.setattr(hls, 'finalize_fragmented_hls', lambda *a, **k: None)
    monkeypatch.setattr(media, 'inspect_dolby', lambda *a, **k: {'ec3': ec3, 'dolby_atmos': True})
    monkeypatch.setattr(hls, 'ec3_configuration', lambda path: ec3)
    monkeypatch.setattr(hls, 'preserve_ec3_configuration', lambda *a, **k: False)
    monkeypatch.setattr(hls, 'stream_copy_digests', lambda path, **k: {'video': 'unchanged', 'audio':
        'changed' if problem == 'packets' and path.suffix == '.m3u8' else 'unchanged'})
    if problem == 'none':
        result = hls.package_variant(db, archive.id)
        assert result.metadata_json['hls_audio_probe']['probesize_bytes'] == 33554432
        assert result.metadata_json['payloads_verified']
        assert result.metadata_json['fps'] == pytest.approx(60000 / 1001)
        assert result.metadata_json['video_bitrate_bps'] == 8_000_000
        assert result.metadata_json['audio_bitrate_bps'] == 1_024_000
        assert result.metadata_json['total_bitrate_bps'] == len(b'archive fixture') * 8 / 600
        assert len(calls) == 3 and calls[0][1] is False
        assert calls[1:] == [('index.m3u8', False), ('index.m3u8', True)]
    else:
        with pytest.raises(tools.PlaybackError, match='channels' if problem == 'channels' else 'payload'):
            hls.package_variant(db, archive.id)
        assert not list(db.scalars(select(MediaVariant).where(MediaVariant.kind == 'hls')))
    assert ('index.m3u8', True) in calls


@pytest.mark.skipif(not os.environ.get('TREASURE_TEST_HLS_EC3_ARCHIVE'), reason='explicit read-only real 4K EC-3 archive required')
def test_real_high_bitrate_eight_channel_hls_requires_extended_probe_and_preserves_packets(db):
    source = Path(os.environ['TREASURE_TEST_HLS_EC3_ARCHIVE'])
    before = file_digest(source)
    probe = tools.probe_media(source)
    audio = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
    assert audio['codec_name'] == 'eac3' and audio['channels'] == 8
    video, part, archive = archive_fixture(db)
    asset = ingest_file(db, source, kind='media', mime_type='video/mp4')
    archive.asset_id, archive.duration = asset.id, float(probe['format']['duration'])
    archive.video_codec, archive.audio_codec = 'hevc', 'eac3'
    db.commit()
    result = hls.package_variant(db, archive.id)
    data = result.metadata_json
    assert data['hls_audio_probe']['probesize_bytes'] == 33554432
    assert data['audio_channels'] == 8 and data['ec3_configuration_verified'] and data['dolby_atmos']
    assert data['payloads_verified'] and set(data['payload_hashes']) == {'audio', 'video'}
    assert data['dovi'] and data['dolby_vision']
    assert file_digest(source) == before
