"""Fail closed on tool errors and changed ordinary HLS audio before publication."""
from pathlib import Path
import sys

import pytest
from sqlalchemy import select

from app.models import MediaVariant
from app.playback import hls, tools
from test_ingest_runner import db  # noqa: F401
from test_playback_derivatives import archive_fixture


def test_strict_tool_rejects_diagnostics_even_with_success_exit():
    diagnostic = "Error decoding AAC frame header; private-source-path"
    with pytest.raises(tools.PlaybackError, match="reported errors") as error:
        tools.run_tool([sys.executable, "-c", f"import sys; sys.stderr.write({diagnostic!r})"],
                       timeout=5, reject_errors=True)
    assert "private-source-path" not in str(error.value)


def test_tool_stderr_remains_available_for_measurements():
    measurement = '{"input_i": -18, "input_tp": -1}'
    output, errors = tools.run_tool([sys.executable, "-c", f"import sys; sys.stderr.write({measurement!r})"],
                                    timeout=5, capture=True)
    assert output == b"" and errors.decode() == measurement
    assert tools.run_tool([sys.executable, "-c", "print('ok')"], timeout=5,
                          capture=True, reject_errors=True)[0].strip() == b"ok"


def test_probe_and_packet_verifiers_request_strict_diagnostics(monkeypatch):
    calls = []
    def tool(args, **kwargs):
        calls.append((args, kwargs))
        return (b'{"streams":[]}' if '-show_streams' in args else b'SHA256=' + b'a' * 64), b''
    monkeypatch.setattr(tools, 'run_tool', tool)
    monkeypatch.setattr(hls, 'run_tool', tool)
    tools.probe_media(Path('input.mp4'))
    hls.packet_digest(Path('input.mp4'), 'audio')
    assert len(calls) == 2 and all(options['reject_errors'] for _, options in calls)


@pytest.mark.parametrize('codec', ['aac', 'ac3', None])
@pytest.mark.parametrize('changed', [False, True])
def test_all_hls_codecs_verify_payload_once_per_input_before_publication(db, monkeypatch, codec, changed):
    _, _, archive = archive_fixture(db)
    streams = [{'codec_type': 'video', 'index': 0, 'codec_name': 'h264'}]
    if codec:
        streams.append({'codec_type': 'audio', 'index': 1, 'codec_name': codec,
                        'sample_rate': '48000', 'channels': 2})
    monkeypatch.setattr(hls, 'probe_media', lambda *a, **k: {'streams': streams, 'format': {'duration': 600}})
    def mux(args, **kwargs):
        assert kwargs['reject_errors']
        folder = Path(args[-1]).parent
        (folder / 'init.mp4').write_bytes(b'init')
        (folder / 'segment_000000.m4s').write_bytes(b'segment')
        (folder / 'index.m3u8').write_text(
            '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:600,\nsegment_000000.m4s\n#EXT-X-ENDLIST\n')
    calls = []
    def digests(path, **kwargs):
        calls.append((path, kwargs))
        assert kwargs['include_audio'] == bool(codec)
        result = {'video': 'v'}
        if codec:
            result['audio'] = 'a'
        if changed and path.suffix == '.m3u8':
            result['audio' if codec else 'video'] = 'changed'
        return result
    monkeypatch.setattr(hls, 'run_tool', mux)
    monkeypatch.setattr(hls, 'fragment_movie_timescale', lambda _: 48000)
    monkeypatch.setattr(hls, 'finalize_fragmented_hls', lambda *a, **k: None)
    monkeypatch.setattr(hls, 'stream_copy_digests', digests)
    if changed:
        with pytest.raises(tools.PlaybackError, match='payload'):
            hls.package_variant(db, archive.id)
        assert not db.scalar(select(MediaVariant).where(MediaVariant.kind == 'hls'))
    else:
        result = hls.package_variant(db, archive.id)
        assert result.metadata_json['payloads_verified']
        assert set(result.metadata_json['payload_hashes']) == ({'video', 'audio'} if codec else {'video'})
    assert len(calls) == 2 and calls[0][0].name == 'source.media' and calls[1][0].suffix == '.m3u8'


def test_video_only_stream_hash_has_no_audio_map_and_checks_extra_streams(monkeypatch):
    commands = []
    def tool(args, **kwargs):
        commands.append((args, kwargs))
        return b'0,v,SHA256=' + b'a' * 64, b''
    monkeypatch.setattr(hls, 'run_tool', tool)
    assert hls.stream_copy_digests(Path('silent.mp4'), include_audio=False) == {'video': 'a' * 64}
    assert '0:a:0' not in commands[0][0] and commands[0][1]['reject_errors']
    monkeypatch.setattr(hls, 'run_tool', lambda *a, **k: (b'0,v,SHA256=' + b'a' * 64 +
        b'\n1,a,SHA256=' + b'b' * 64, b''))
    with pytest.raises(tools.PlaybackError, match='valid digests'):
        hls.stream_copy_digests(Path('silent.mp4'), include_audio=False)
