"""Shared-context MP4 structure and real multi-moof packet-preservation checks."""
from fractions import Fraction
import csv
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

from app.config import settings
from app.playback import fragments
from app.playback.hls import parse_generated_playlist, stream_copy_digests
from app.playback.tools import PlaybackError, probe_media


def box(kind, data=b''):
    return struct.pack('>I4s', len(data)+8, kind)+data


def moof(sequence, timestamp, *, flags=0x020000, track=1):
    tfhd = box(b'tfhd', flags.to_bytes(4, 'big')+struct.pack('>I', track))
    tfdt = box(b'tfdt', b'\x01\0\0\0'+struct.pack('>Q', timestamp))
    trun = box(b'trun', b'\0\0\x01\0'+struct.pack('>II', 1, 1000))
    return box(b'moof', box(b'mfhd', struct.pack('>II', 0, sequence))+box(b'traf', tfhd+tfdt+trun))


def fixture(folder, *, second_time=2000):
    init = box(b'ftyp', b'isom')+box(b'moov', b'fixture')
    first = moof(1, 0)+box(b'mdat', b'first-packet')+moof(2, 1000)+box(b'mdat', b'second-packet')
    second = moof(3, second_time)+box(b'mdat', b'third-packet')
    (folder/'segment_000000.m4s').write_bytes(init+first)
    (folder/'segment_000001.m4s').write_bytes(second+box(b'mfra', b'obsolete-global-offset-index'))
    (folder/'segments.csv').write_text('segment_000000.m4s,0.000000,2.000000\nsegment_000001.m4s,2.000000,3.000000\n')
    return init, first, second


def test_shared_init_split_preserves_every_media_byte_and_decode_time(tmp_path, monkeypatch):
    init, first, second = fixture(tmp_path)
    # Large media must never be loaded through the convenience full-file API.
    monkeypatch.setattr(Path, 'read_bytes', lambda *a: pytest.fail('unbounded read_bytes'))
    playlist = fragments.finalize_fragmented_hls(tmp_path)
    with (tmp_path/'init.mp4').open('rb') as source:
        assert source.read() == init
    for n, expected in enumerate((first, second)):
        with (tmp_path/f'segment_{n:06d}.m4s').open('rb') as source:
            assert source.read() == expected
    _, rows = parse_generated_playlist(playlist.read_text())
    assert [row['duration'] for row in rows] == [2, 1]
    with pytest.raises(PlaybackError, match='already exists'):
        fragments.finalize_fragmented_hls(tmp_path)


@pytest.mark.parametrize('value', ['../escape.m4s', '/absolute.m4s', 'https://outside/segment_000000.m4s',
    'segment_000001.m4s', 'segment_000000.m4s,0,nan', 'segment_000000.m4s,0,inf'])
def test_csv_rejects_external_paths_wrong_sequence_and_nonfinite_time(tmp_path, value):
    fixture(tmp_path)
    (tmp_path/'segments.csv').write_text(value if ',0,' in value else f'{value},0,1\n')
    with pytest.raises(PlaybackError, match='fragment list'):
        fragments.finalize_fragmented_hls(tmp_path)
    assert not (tmp_path/'index.m3u8').exists()


def test_csv_variable_frame_packet_ends_do_not_override_keyframe_boundaries(tmp_path):
    # Observed from a 1/16000 HEVC clock / 44.1 kHz AAC archive. FFmpeg's CSV
    # end is max(packet PTS+duration), not the next keyframe PTS: both overlap
    # and gap are valid here, without dropping, duplicating or retiming packets.
    (tmp_path/'segments.csv').write_text(
        'segment_000000.m4s,0.000000,10.048625\n'
        'segment_000001.m4s,10.048000,15.047625\n'
        'segment_000002.m4s,15.048000,20.046625\n'
        'segment_000003.m4s,20.047000,25.045625\n')
    rows = fragments._read_csv(tmp_path, lambda: None)
    assert [row['duration'] for row in rows] == [10.048, 5.0, 4.999, 4.998625]
    assert sum(row['duration'] for row in rows) == pytest.approx(25.045625)


@pytest.mark.parametrize('next_start,next_end', [('0', '3'), ('2', '2'), ('1', '1.5'), ('86401','86402')])
def test_csv_nonadvancing_boundaries_remain_invalid(tmp_path, next_start, next_end):
    (tmp_path/'segments.csv').write_text('segment_000000.m4s,0,2\n'
        f'segment_000001.m4s,{next_start},{next_end}\n')
    with pytest.raises(PlaybackError, match='fragment list'):
        fragments._read_csv(tmp_path, lambda: None)


@pytest.mark.parametrize('payload', [b'short', struct.pack('>I4s', 0, b'ftyp'),
    struct.pack('>I4sQ', 1, b'ftyp', 2**63), struct.pack('>I4s', 99999, b'ftyp'),
    box(b'ftyp')+box(b'moov')+box(b'mdat', b'no-moof'),
    box(b'ftyp')+box(b'moov')+moof(1, 0, flags=1)+box(b'mdat', b'x')])
def test_malformed_and_absolute_offset_boxes_are_rejected(tmp_path, payload):
    fixture(tmp_path)
    (tmp_path/'segment_000000.m4s').write_bytes(payload)
    with pytest.raises(PlaybackError):
        fragments.finalize_fragmented_hls(tmp_path)
    assert not (tmp_path/'init.mp4').exists()


def test_tfdt_reset_or_repeated_sequence_is_rejected_before_rewrite(tmp_path):
    fixture(tmp_path, second_time=1000)
    with pytest.raises(PlaybackError, match='timestamps'):
        fragments.finalize_fragmented_hls(tmp_path)
    assert not (tmp_path/'init.mp4').exists()
    fixture(tmp_path)
    (tmp_path/'segment_000001.m4s').write_bytes(moof(2, 2000)+box(b'mdat', b'x'))
    with pytest.raises(PlaybackError, match='out of order'):
        fragments.finalize_fragmented_hls(tmp_path)


@pytest.mark.parametrize('kind,limit', [(b'ftyp', fragments.MAX_INIT_BYTES), (b'moof', fragments.MAX_METADATA_BYTES)])
def test_oversized_control_boxes_fail_without_buffering_their_payload(tmp_path, kind, limit):
    fixture(tmp_path)
    prefix = b'' if kind == b'ftyp' else box(b'ftyp')+box(b'moov')
    path = tmp_path/'segment_000000.m4s'
    # A sparse file represents a large claimed box without allocating its body
    # in Python or reading it back. The parser must reject the header alone.
    with path.open('wb') as source:
        source.write(prefix+struct.pack('>I4s', limit+1, kind))
        source.truncate(len(prefix)+limit+1)
    with pytest.raises(PlaybackError, match='initialization|metadata exceeds'):
        fragments.finalize_fragmented_hls(tmp_path)
    assert not (tmp_path/'init.mp4').exists()


def test_segment_and_box_count_limits_and_cancellation(tmp_path, monkeypatch):
    fixture(tmp_path)
    monkeypatch.setattr(fragments, 'MAX_SEGMENTS', 1)
    with pytest.raises(PlaybackError, match='limits'):
        fragments.finalize_fragmented_hls(tmp_path)
    monkeypatch.setattr(fragments, 'MAX_SEGMENTS', 100)
    monkeypatch.setattr(fragments, 'MAX_BOXES', 1)
    with pytest.raises(PlaybackError, match='count'):
        fragments.finalize_fragmented_hls(tmp_path)
    def cancel():
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        fragments.finalize_fragmented_hls(tmp_path, check_active=cancel)
    assert not (tmp_path/'index.m3u8').exists()


def test_symlink_fragment_is_never_followed(tmp_path):
    fixture(tmp_path)
    victim = tmp_path/'unrelated'
    victim.write_bytes(b'private')
    target = tmp_path/'segment_000000.m4s'
    target.unlink()
    try:
        target.symlink_to(victim)
    except OSError:
        pytest.skip('Symlink creation is unavailable')
    with pytest.raises(PlaybackError, match='private files'):
        fragments.finalize_fragmented_hls(tmp_path)
    assert victim.read_bytes() == b'private'


def test_copy_is_bounded_and_cancellable():
    class Source(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= fragments.COPY_BYTES
            return super().read(size)
    payload = b'x'*(3*fragments.COPY_BYTES+1)
    calls = []
    result = io.BytesIO()
    fragments._copy_range(Source(payload), result, 0, len(payload), lambda: calls.append(True))
    assert result.getvalue() == payload and len(calls) == 4


def test_timescale_covers_both_tracks_without_rounding():
    assert fragments.fragment_movie_timescale([{'time_base':'1/12800'}, {'time_base':'1/44100'}]) == 5644800
    assert fragments.fragment_movie_timescale([{'time_base':'1/16000'}, {'time_base':'1/48000'}]) == 48000
    for value in ([], [{'time_base':'1/0'}], [{'time_base':'-1/2'}],
                  [{'time_base':'1/2147483647'}, {'time_base':'1/3'}]):
        with pytest.raises(PlaybackError, match='time bases'):
            fragments.fragment_movie_timescale(value)


def _run(args):
    result = subprocess.run(args, capture_output=True, timeout=120, check=True)
    assert not result.stderr, result.stderr.decode(errors='replace')
    return result.stdout


def _packets(path):
    result = json.loads(_run([str(settings.ffprobe_path), '-v', 'error', '-show_packets', '-show_streams',
        '-show_data_hash', 'sha256', '-of', 'json', str(path)]))
    streams = {s['index']:s for s in result['streams']}
    rows = {}
    for packet in result['packets']:
        stream = streams[packet['stream_index']]
        scale = Fraction(stream['time_base'])
        rows.setdefault(stream['codec_type'], []).append((packet['data_hash'], int(packet['pts'])*scale,
            int(packet['dts'])*scale, packet.get('flags', ''),
            int(packet['duration'])*scale if packet.get('duration') else None))
    return rows


@pytest.mark.skipif(not shutil.which(str(settings.ffmpeg_path)) or not shutil.which(str(settings.ffprobe_path)),
                    reason='Real FFmpeg and FFprobe required')
@pytest.mark.parametrize('codec,rate,vfr', [('aac',48000,False), ('aac',44100,False), ('eac3',48000,False),
    ('flac',48000,False), (None,48000,False), ('aac',44100,True)])
def test_real_long_gop_multiple_fragments_preserve_packets_and_global_timeline(tmp_path, codec, rate, vfr):
    source = tmp_path/'source.mp4'
    fps = 60 if vfr else 25
    arguments = [str(settings.ffmpeg_path), '-nostdin','-v','error','-f','lavfi','-i',f'testsrc2=size=128x72:rate={fps}']
    if codec:
        arguments += ['-f','lavfi','-i',f'sine=frequency=997:sample_rate={rate}']
    arguments += ['-t','22','-c:v','libx264','-threads','2','-g',str(fps*10),'-keyint_min',str(fps*10),'-sc_threshold','0']
    if vfr:
        # Match the real archive's millisecond-quantized ~60 fps PTS in a
        # 1/16000 track clock, rather than fabricating a malformed CSV alone.
        arguments += ['-vf','settb=1/16000,setpts=floor(N*16.663)*16',
                      '-fps_mode','passthrough','-enc_time_base','1:16000']
    if codec:
        arguments += ['-c:a',codec,'-ac','6' if codec=='eac3' else '2','-b:a','640k' if codec=='eac3' else '192k']
    _run([*arguments,'-strict','unofficial','-movflags','+faststart',str(source)])
    output = tmp_path/'output'; output.mkdir()
    streams = probe_media(source)['streams']
    command = [str(settings.ffmpeg_path),'-nostdin','-v','error','-n','-i',str(source),'-map','0:v:0']
    if codec:
        command += ['-map','0:a:0']
    _run([*command,'-c','copy', *fragments.segment_mux_options(output, segment_seconds=6,
        movie_timescale=fragments.fragment_movie_timescale(streams))])
    if vfr:
        rows = list(csv.reader((output/'segments.csv').read_text().splitlines()))
        assert any(Fraction(a[2]) != Fraction(b[1]) for a,b in zip(rows, rows[1:])), 'Fixture must reproduce CSV boundary mismatch'
    playlist = fragments.finalize_fragmented_hls(output)
    init_boxes = list(fragments._children((output/'init.mp4').read_bytes()))
    assert init_boxes[0][0] == b'ftyp'
    brands = bytes(init_boxes[0][1])[8:]
    assert b'iso6' in [brands[i:i+4] for i in range(0, len(brands), 4)], 'RFC 8216 fMP4 requires iso6 compatibility'
    _, segments = parse_generated_playlist(playlist.read_text())
    assert len(segments) >= 3
    assert stream_copy_digests(source, include_audio=bool(codec)) == stream_copy_digests(playlist, include_audio=bool(codec))
    before, after = _packets(source), _packets(playlist)
    shared_offset = None
    for kind, rows in before.items():
        assert [p[0] for p in rows] == [p[0] for p in after[kind]]
        pts_offsets = {b[1]-a[1] for a,b in zip(rows,after[kind])}
        dts_offsets = {b[2]-a[2] for a,b in zip(rows,after[kind])}
        assert pts_offsets == dts_offsets and len(pts_offsets) == 1
        offset = next(iter(pts_offsets))
        if shared_offset is not None:
            if vfr:
                # A 33 ms negative-video-DTS shift is not an integer number
                # of 44.1 kHz samples. Permit only the one-time, one-sample
                # clock rounding; every packet in each track remains exact.
                assert abs(offset-shared_offset) <= Fraction(1, rate)
            else:
                assert offset == shared_offset, 'Audio/video relative sync changed'
        shared_offset = offset
        # FFprobe may omit fMP4 packet duration, and AAC's trimmed tail may
        # restore one encoded frame. Every interior timestamp remains exact.
        for i in range(1, len(rows)-1):
            assert after[kind][i+1][1]-after[kind][i][1] == rows[i+1][1]-rows[i][1]
    for number, row in enumerate(segments):
        path = output/row['filename']
        boxes = list(fragments._children(path.read_bytes()))
        if number < 2:
            assert sum(kind == b'moof' for kind,_ in boxes) >= 2
        first_pair = b''.join(box(kind, bytes(body)) for kind,body in boxes[:2])
        partial = tmp_path/f'partial-{number}.mp4'
        partial.write_bytes((output/'init.mp4').read_bytes()+first_pair)
        available = _packets(partial)
        assert 'K' in available['video'][0][3]
        assert 1 <= len(available['video']) <= fps+1
