"""Real lossless FLAC fMP4 HLS and publication guards; no private media needed."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import MediaVariant
from app.playback import hls
from app.playback.tools import PlaybackError, probe_media
from app.storage.base import file_digest
from app.storage.service import ingest_file, materialize_asset
from test_ingest_runner import db
from test_playback_derivatives import archive_fixture


def pcm_digest(path):
    # Regression only: production proves compressed packets + STREAMINFO instead
    # of decoding hours of lossless audio again merely to compare PCM.
    return subprocess.run([str(settings.ffmpeg_path), '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                           '-c:a', 'pcm_s32le', '-f', 'hash', '-hash', 'sha256', '-'],
                          check=True, capture_output=True, timeout=60).stdout


@pytest.mark.skipif(not shutil.which(str(settings.ffmpeg_path)) or not shutil.which(str(settings.ffprobe_path)),
                    reason='FFmpeg integration tools unavailable')
@pytest.mark.parametrize('codec,depth,rate,channels,hdr', [
    ('h264', 16, 44100, 2, False), ('hevc', 24, 48000, 2, True), ('hevc', 24, 96000, 6, False),
], ids=['avc-flac16-stereo', 'hdr10-hevc-flac24-stereo', 'hevc-flac24-six-channel'])
def test_real_flac_hls_preserves_packets_pcm_streaminfo_and_properties(db, tmp_path, codec, depth, rate, channels, hdr):
    source = tmp_path / 'flac.mp4'
    args = [str(settings.ffmpeg_path), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=12',
            '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate={rate}', '-t', '4', '-c:v',
            'libx264' if codec == 'h264' else 'libx265', '-threads', '2', '-g', '24', '-preset', 'ultrafast']
    if codec == 'hevc':
        args += ['-x265-params', 'pools=1:frame-threads=1:log-level=error', '-tag:v', 'hvc1']
    if hdr:
        args += ['-pix_fmt', 'yuv420p10le', '-color_primaries', 'bt2020', '-color_trc', 'smpte2084', '-colorspace', 'bt2020nc']
        args[args.index('-x265-params') + 1] += ':colorprim=9:transfer=16:colormatrix=9'
    else:
        args += ['-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709']
    args += ['-c:a', 'flac', '-sample_fmt', 's16' if depth == 16 else 's32', '-bits_per_raw_sample', str(depth),
             '-ac', str(channels), '-movflags', '+faststart+write_colr', str(source)]
    subprocess.run(args, check=True, capture_output=True, timeout=60)
    original_sha = file_digest(source)
    original_probe = probe_media(source)
    _, _, archive = archive_fixture(db)
    asset = ingest_file(db, source, kind='media', mime_type='video/mp4')
    archive.asset_id, archive.video_codec, archive.audio_codec = asset.id, codec, 'flac'
    archive.duration = float(original_probe['format']['duration'])
    db.commit()
    package = hls.package_variant(db, archive.id, segment_seconds=2)
    index = hls.load_hls_index(db, package.asset_id)
    folder = settings.scratch_dir / 'delivered-flac'; folder.mkdir()
    materialize_asset(db, index['init_asset_id'], folder / 'init.mp4')
    lines = ['#EXTM3U', '#EXT-X-VERSION:7', f'#EXT-X-TARGETDURATION:{index["target_duration"]}', '#EXT-X-MAP:URI="init.mp4"']
    for number, segment in enumerate(index['segments']):
        name = f'{number}.m4s'
        materialize_asset(db, segment['asset_id'], folder / name)
        lines.extend([f'#EXTINF:{segment["duration"]},', name])
    playlist = folder / 'index.m3u8'; playlist.write_text('\n'.join([*lines, '#EXT-X-ENDLIST', '']))
    delivered = probe_media(playlist)
    for original in original_probe['streams']:
        actual = next(stream for stream in delivered['streams'] if stream['codec_type'] == original['codec_type'])
        for key in ('codec_name', 'sample_rate', 'channels', 'channel_layout', 'sample_fmt', 'bits_per_raw_sample',
                    'width', 'height', 'pix_fmt', 'color_transfer', 'color_primaries', 'color_space'):
            if key in original:
                assert actual.get(key) == original[key], key
    hashes = {kind: hls.packet_digest(source, kind) for kind in ('video', 'audio')}
    assert hashes == {kind: hls.packet_digest(playlist, kind) for kind in ('video', 'audio')}
    assert package.metadata_json['payload_hashes'] == hashes
    assert pcm_digest(source) == pcm_digest(playlist)
    assert hls.flac_streaminfo_digest(source) == hls.flac_streaminfo_digest(folder / 'init.mp4')
    assert package.metadata_json['flac'] == {'streaminfo_sha256': hls.flac_streaminfo_digest(source),
        'streaminfo_verified': True, 'payload_verified': True, 'bits_per_sample': depth, 'sample_rate': rate, 'channels': channels}
    assert hls.package_variant(db, archive.id, segment_seconds=2).id == package.id
    assert file_digest(source) == original_sha


@pytest.mark.parametrize('problem', ['depth', 'channels', 'streaminfo', 'video_payload', 'audio_payload'])
def test_flac_changed_properties_or_payload_are_never_published(db, monkeypatch, problem):
    _, _, archive = archive_fixture(db)
    def probe(path, **kwargs):
        audio = {'codec_type': 'audio', 'index': 1, 'codec_name': 'flac', 'sample_rate': '48000', 'channels': 2,
                 'channel_layout': 'stereo', 'sample_fmt': 's32', 'bits_per_raw_sample': '24'}
        if path.suffix == '.m3u8':
            if problem == 'depth': audio['bits_per_raw_sample'] = '16'
            if problem == 'channels': audio['channels'] = 1
        return {'streams': [{'codec_type': 'video', 'index': 0, 'codec_name': 'hevc'}, audio], 'format': {'duration': 600}}
    def mux(args, **kwargs):
        assert args[args.index('-strict') + 1] == 'unofficial'
        assert args[args.index('-c') + 1] == 'copy'
        folder = Path(args[-1]).parent
        (folder / 'init.mp4').write_bytes(b'init')
        (folder / 'segment_000000.m4s').write_bytes(b'segment')
        (folder / 'index.m3u8').write_text('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:600,\nsegment_000000.m4s\n#EXT-X-ENDLIST\n')
    def digests(path, **kwargs):
        result = {'video': 'a' * 64, 'audio': 'b' * 64}
        if path.suffix == '.m3u8' and problem.endswith('_payload'):
            result[problem.split('_')[0]] = 'c' * 64
        return result
    monkeypatch.setattr(hls, 'probe_media', probe)
    monkeypatch.setattr(hls, 'run_tool', mux)
    monkeypatch.setattr(hls, 'fragment_movie_timescale', lambda _: 48000)
    monkeypatch.setattr(hls, 'finalize_fragmented_hls', lambda *a, **k: None)
    monkeypatch.setattr(hls, 'stream_copy_digests', digests)
    monkeypatch.setattr(hls, 'flac_streaminfo_digest', lambda path, **kw: 'b' * 64
                        if problem == 'streaminfo' and path.name == 'init.mp4' else 'a' * 64)
    with pytest.raises(PlaybackError):
        hls.package_variant(db, archive.id)
    assert not db.scalar(select(MediaVariant).where(MediaVariant.kind == 'hls'))


@pytest.mark.parametrize('stream', [{'codec_name': 'flac'},
    {'codec_name': 'flac', 'extradata_size': 34, 'extradata_hash': None},
    {'codec_name': 'flac', 'extradata_size': 0, 'extradata_hash': 'SHA256:' + 'a' * 64}])
def test_missing_streaminfo_cannot_compare_equal_and_pass(monkeypatch, stream):
    monkeypatch.setattr(hls, 'run_tool', lambda *a, **k: (json.dumps({'streams': [stream]}).encode(), b''))
    with pytest.raises(PlaybackError, match='STREAMINFO'):
        hls.flac_streaminfo_digest(Path('fixture.mp4'))


@pytest.mark.parametrize('raw', [b'', b'0,v,SHA256=' + b'a' * 64,
    b'0,v,SHA256=' + b'a' * 64 + b'\n0,v,SHA256=' + b'b' * 64,
    b'0,v,SHA256=' + b'a' * 64 + b'\n1,a,SHA256=' + b'b' * 64 + b'\n1,a,SHA256=' + b'c' * 64])
def test_stream_hash_rejects_missing_duplicate_or_extra_streams(monkeypatch, raw):
    monkeypatch.setattr(hls, 'run_tool', lambda *a, **k: (raw, b''))
    with pytest.raises(PlaybackError, match='valid digests'):
        hls.stream_copy_digests(Path('fixture.mp4'))
