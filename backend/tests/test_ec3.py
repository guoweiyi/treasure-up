import struct

import pytest

from app.playback.ec3 import ec3_configuration, elementary_joc_complexity, parse_dec3, preserve_ec3_configuration, _shift_chunk_offsets
from app.playback.tools import PlaybackError


def box(kind, body):
    return struct.pack(">I4s", len(body) + 8, kind) + body


def mp4(config):
    entry = box(b"ec-3", b"\0" * 28 + box(b"dec3", config))
    data = box(b"stsd", b"\0" * 4 + struct.pack(">I", 1) + entry)
    for kind in (b"stbl", b"minf", b"mdia", b"trak", b"moov"):
        data = box(kind, data)
    return box(b"ftyp", b"isom\0\0\0\0") + data


def test_joc_complexity_comes_from_dec3_not_channel_count(tmp_path):
    # 5.1 layout, one independent stream, JOC decoding complexity 16.
    payload = bytes.fromhex("2000200f000110")
    path = tmp_path / "audio.mp4"
    path.write_bytes(mp4(payload))
    result = ec3_configuration(path)
    assert result["joc"] is True
    assert result["complexity_index_type_a"] == 16
    assert result["substreams"] == [[0, 16, 0, 0, 7, 1, 0, 0]]
    # The same E-AC-3 channel layout without the JOC extension is not Atmos.
    assert parse_dec3(payload[:5])["joc"] is False
    assert parse_dec3(payload[:5])["complexity_index_type_a"] is None


@pytest.mark.parametrize("payload", [b"", b"\0" * 4, bytes.fromhex("2000200f0001"), bytes.fromhex("2000200f000100")])
def test_rejects_truncated_or_invalid_joc(payload):
    with pytest.raises(PlaybackError):
        parse_dec3(payload)


def test_mp4_atom_bounds_and_ambiguous_tracks_are_rejected(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(box(b"ftyp", b"isom") + struct.pack(">I4s", 1000, b"moov"))
    with pytest.raises(PlaybackError, match="bounds"):
        ec3_configuration(path)
    content = mp4(bytes.fromhex("2000200f000110"))
    path.write_bytes(content + content[16:])
    with pytest.raises(PlaybackError, match="Ambiguous"):
        ec3_configuration(path)


def test_configuration_reader_skips_unrelated_media_body(tmp_path):
    path = tmp_path / "media.mp4"
    # A fake dec3 inside compressed media must never become signaling evidence.
    path.write_bytes(box(b"ftyp", b"isom") + box(b"mdat", b"junk dec3\x01\x10"))
    assert ec3_configuration(path) == {}


def scratch_mp4(*, faststart=True, width=4):
    payload = b"unchanged-encoded-audio-packets"
    ftyp, mdat = box(b"ftyp", b"isom"), box(b"mdat", payload)
    entry = box(b"ec-3", b"\0" * 28 + box(b"dec3", bytes.fromhex("2000200f00")))
    stsd = box(b"stsd", b"\0" * 4 + struct.pack(">I", 1) + entry)
    def moov(offset):
        offsets = box(b"stco" if width == 4 else b"co64", b"\0" * 4 + struct.pack(">I", 1) + offset.to_bytes(width, "big"))
        data = box(b"stbl", stsd + offsets)
        for kind in (b"minf", b"mdia", b"trak", b"moov"):
            data = box(kind, data)
        return data
    offset = len(ftyp) + (len(moov(0)) if faststart else 0) + 8
    data = ftyp + moov(offset) + mdat if faststart else ftyp + mdat + moov(offset)
    return data, payload


@pytest.mark.parametrize("faststart", [True, False])
@pytest.mark.parametrize("width", [4, 8])
def test_scratch_repair_preserves_payload_and_chunk_offsets_and_is_idempotent(tmp_path, faststart, width):
    original, payload = scratch_mp4(faststart=faststart, width=width)
    path = tmp_path / "new-mux.mp4"
    path.write_bytes(original)
    reference = parse_dec3(bytes.fromhex("2000200f000110"))
    assert preserve_ec3_configuration(path, reference) is True
    repaired = path.read_bytes()
    assert len(repaired) == len(original) + 2
    assert ec3_configuration(path) == reference
    start = repaired.index(b"stco" if width == 4 else b"co64") + 12
    offset = int.from_bytes(repaired[start:start + width], "big")
    assert repaired[offset:offset + len(payload)] == payload
    assert preserve_ec3_configuration(path, reference) is False
    assert path.read_bytes() == repaired
    assert not list(tmp_path.glob(".ec3-*"))


def test_cancelled_repair_keeps_original_scratch_file_and_removes_temporary(tmp_path):
    path = tmp_path / "new-mux.mp4"
    original, _ = scratch_mp4()
    path.write_bytes(original)
    checks = 0
    def cancel():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        preserve_ec3_configuration(path, parse_dec3(bytes.fromhex("2000200f000110")), check_active=cancel)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".ec3-*"))


def test_chunk_offset_overflow_or_offsets_inside_metadata_fail_closed():
    table = b"\0" * 4 + struct.pack(">II", 1, 2**32 - 1)
    with pytest.raises(PlaybackError, match="overflow"):
        _shift_chunk_offsets(table, 4, 2, 2**32 + 10, 16, 100)
    table = b"\0" * 4 + struct.pack(">II", 1, 20)
    with pytest.raises(PlaybackError, match="offset"):
        _shift_chunk_offsets(table, 4, 2, 1000, 16, 100)


def test_repair_does_not_overwrite_a_different_joc_configuration(tmp_path):
    path = tmp_path / "different.mp4"
    original = mp4(bytes.fromhex("2000200f000108"))
    path.write_bytes(original)
    with pytest.raises(PlaybackError, match="changed"):
        preserve_ec3_configuration(path, parse_dec3(bytes.fromhex("2000200f000110")))
    assert path.read_bytes() == original


def eac3_frame(complexity=8, *, dependent=False, extension=True):
    fields = [(0x0b77, 16), (int(dependent), 2), (0, 3), (63, 11),
              (0, 2), (3, 2), (7, 3), (1, 1), (16, 5), (0, 5), (0, 1)]
    if dependent:
        fields.extend([(1, 1), (0x800, 16)])
    fields.extend([(0, 1), (0, 1), (int(extension), 1)])
    if extension:
        fields.extend([(1, 6), (1, 8), (complexity, 8)])
    bits = ''.join(f'{value:0{width}b}' for value, width in fields)
    return int(bits.ljust(128 * 8, '0'), 2).to_bytes(128, 'big')


def test_elementary_complexity_is_measured_from_independent_or_dependent_bsi():
    # Real failure's topology: 48 kHz 640 kbps AC-3 core + dependent E-AC-3.
    # A different complexity (8) proves that 16 is not a hardcoded fallback.
    core = bytes.fromhex('0b7700002430e0') + b'\0' * (2560 - 7)
    assert elementary_joc_complexity(core + eac3_frame(8, dependent=True)) == 8
    assert elementary_joc_complexity(eac3_frame(16) * 2) == 16
    assert elementary_joc_complexity(core + eac3_frame(extension=False)) is None


@pytest.mark.parametrize('data', [b'', b'garbage' + eac3_frame(), eac3_frame()[:-1],
    eac3_frame(0), eac3_frame(8) + eac3_frame(16),
    bytes.fromhex('0b770000ff30e0'), eac3_frame()[:6]])
def test_elementary_joc_parser_rejects_unbounded_truncated_or_conflicting_data(data):
    with pytest.raises(PlaybackError):
        elementary_joc_complexity(data)


def test_elementary_parser_never_treats_audio_payload_as_joc_header():
    frame = bytearray(eac3_frame(extension=False))
    frame[32:48] = eac3_frame(16)[:16]
    assert elementary_joc_complexity(bytes(frame)) is None
