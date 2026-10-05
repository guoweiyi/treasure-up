"""Read EC-3 sample-entry signaling without scanning compressed media payloads.

The dec3 bit fields follow ETSI TS 102 366 / the EC3SpecificBox. The JOC
complexity value is not a speaker/channel count (Apple's HLS CHANNELS n/JOC).
"""
from pathlib import Path
import os
import struct
import tempfile

from .tools import PlaybackError


class _Bits:
    def __init__(self, data):
        self.data, self.position = data, 0

    def take(self, count):
        start, end = self.position, self.position + count
        if count < 0 or end > len(self.data) * 8:
            raise PlaybackError("Truncated EC-3 bitstream header")
        self.position = end
        if not count:
            return 0
        chunk = int.from_bytes(self.data[start // 8:(end + 7) // 8], "big")
        return (chunk >> ((8 - end % 8) % 8)) & ((1 << count) - 1)


def _frame_joc(frame):
    """Read E-AC-3 BSI through addbsi (ETSI TS 102 366, E.1.2.2).

    Only syntax fields are walked: never search encoded audio for a JOC marker.
    A dependent frame can carry JOC even when its parent is ordinary AC-3.
    """
    bits = _Bits(frame)
    bits.take(16)  # syncword was checked by the frame walker
    kind, substream = bits.take(2), bits.take(3)
    bits.take(11)
    if kind == 3 or substream:
        raise PlaybackError("Unsupported EC-3 substream header")
    sample_rate = bits.take(2)
    if sample_rate == 3:
        if bits.take(2) == 3:
            raise PlaybackError("Invalid EC-3 sample rate")
        blocks = 6
    else:
        blocks = (1, 2, 3, 6)[bits.take(2)]
    channels, lfe = bits.take(3), bits.take(1)
    if not 11 <= bits.take(5) <= 16:
        raise PlaybackError("Invalid EC-3 bitstream identifier")
    for _ in range(1 if channels else 2):
        bits.take(5)  # dialnorm
        if bits.take(1):
            bits.take(8)  # compression
    if kind == 1 and bits.take(1):
        bits.take(16)  # dependent channel map
    if bits.take(1):  # mixing metadata
        if channels > 2:
            bits.take(2)
            if channels & 1:
                bits.take(6)
            if channels & 4:
                bits.take(6)
        if lfe and bits.take(1):
            bits.take(5)
        if kind == 0:
            for _ in range(1 if channels else 2):
                if bits.take(1):
                    bits.take(6)
            if bits.take(1):
                bits.take(6)
            mixing = bits.take(2)
            if mixing == 1:
                bits.take(5)
            elif mixing == 2:
                bits.take(12)
            elif mixing == 3:
                bits.take((bits.take(5) + 2) * 8)
            if channels < 2:
                for _ in range(1 if channels else 2):
                    if bits.take(1):
                        bits.take(14)
            if bits.take(1):
                for _ in range(blocks):
                    if blocks == 1 or bits.take(1):
                        bits.take(5)
    if bits.take(1):  # informational metadata
        bits.take(5)
        if channels == 2:
            bits.take(4)
        if channels >= 6:
            bits.take(2)
        for _ in range(1 if channels else 2):
            if bits.take(1):
                bits.take(8)
        if sample_rate != 3:
            bits.take(1)
    if kind == 0 and blocks != 6:
        bits.take(1)  # converter sync
    if kind == 2 and (blocks == 6 or bits.take(1)):
        bits.take(6)
    if not bits.take(1):  # addbsie
        return None
    length = bits.take(6) + 1
    additional = bytes(bits.take(8) for _ in range(length))
    if not additional[0] & 1:
        return None
    if length < 2 or not additional[1]:
        raise PlaybackError("Invalid EC-3 bitstream JOC complexity")
    return additional[1]


def elementary_joc_complexity(data):
    """Return the measured JOC index from a bounded complete raw audio sample.

    AC-3 frame sizes follow TS 102 366 table 5.18, including the 44.1 kHz
    alternating word. Do not scan for syncwords after a malformed frame.
    """
    if not data or len(data) > 16 * 1024**2:
        raise PlaybackError("Invalid EC-3 verification sample size")
    rates = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512, 576, 640)
    position, frames, indices = 0, 0, set()
    while position < len(data):
        frames += 1
        if frames > 8192 or len(data) - position < 7 or data[position:position + 2] != b"\x0b\x77":
            raise PlaybackError("Invalid EC-3 verification frame boundary")
        header = data[position:position + 7]
        bsid = header[5] >> 3
        if bsid <= 10:
            rate, size_code = header[4] >> 6, header[4] & 63
            if rate == 3 or size_code > 37:
                raise PlaybackError("Invalid AC-3 frame size")
            bitrate = rates[size_code // 2]
            words = (bitrate * 2, bitrate * 320 // 147 + size_code % 2, bitrate * 3)[rate]
            size = words * 2
        elif bsid <= 16:
            size = ((((header[2] & 7) << 8) | header[3]) + 1) * 2
        else:
            raise PlaybackError("Invalid EC-3 bitstream identifier")
        if size < 7 or position + size > len(data):
            raise PlaybackError("Truncated EC-3 verification frame")
        if bsid > 10:
            index = _frame_joc(data[position:position + size])
            if index is not None:
                indices.add(index)
        position += size
    if len(indices) > 1:
        raise PlaybackError("Inconsistent EC-3 JOC complexity in sample")
    return next(iter(indices), None)


def parse_dec3(data):
    if not 5 <= len(data) <= 256:
        raise PlaybackError("Invalid EC-3 configuration size")
    bits = int.from_bytes(data, "big")
    position = 0

    def take(count):
        nonlocal position
        if position + count > len(data) * 8:
            raise PlaybackError("Truncated EC-3 configuration")
        position += count
        return (bits >> (len(data) * 8 - position)) & ((1 << count) - 1)

    take(13)  # Data rate may change when remuxing; it does not signal JOC.
    substreams = take(3) + 1
    layouts = []
    for _ in range(substreams):
        fscod, bsid = take(2), take(5)
        take(1)
        asvc, bsmod, acmod, lfeon = take(1), take(3), take(3), take(1)
        take(3)
        dependent = take(4)
        if dependent:
            channel_location = take(9)
        else:
            take(1)
            channel_location = 0
        layouts.append([fscod, bsid, asvc, bsmod, acmod, lfeon, dependent, channel_location])
    joc, complexity = False, None
    if position < len(data) * 8:
        take(7)
        joc = bool(take(1))
        if joc:
            complexity = take(8)
            if not complexity:
                raise PlaybackError("Invalid EC-3 JOC complexity")
    return {"joc": joc, "complexity_index_type_a": complexity, "substreams": layouts}


def ec3_configuration(path: Path):
    """Read at most bounded box headers and dec3, never an mdat body."""
    size = path.stat().st_size
    remaining = 4096
    found = []
    with path.open("rb") as stream:
        def boxes(start, end, route):
            nonlocal remaining
            while start < end:
                remaining -= 1
                if remaining < 0 or end - start < 8:
                    raise PlaybackError("Invalid MP4 audio configuration structure")
                stream.seek(start)
                length, kind = struct.unpack(">I4s", stream.read(8))
                header = 8
                if length == 1:
                    if end - start < 16:
                        raise PlaybackError("Truncated MP4 audio configuration")
                    length = struct.unpack(">Q", stream.read(8))[0]
                    header = 16
                elif length == 0:
                    length = end - start
                if length < header or start + length > end:
                    raise PlaybackError("Invalid MP4 audio configuration bounds")
                body, finish = start + header, start + length
                if route and kind == route[0]:
                    skip = 8 if kind == b"stsd" else 28 if kind == b"ec-3" else 0
                    if body + skip > finish:
                        raise PlaybackError("Truncated MP4 audio sample entry")
                    if kind == b"ec-3":
                        stream.seek(body + 8)
                        if stream.read(2) != b"\0\0":
                            raise PlaybackError("Unsupported EC-3 sample entry version")
                    if len(route) > 1:
                        boxes(body + skip, finish, route[1:])
                    else:
                        if finish - body > 256:
                            raise PlaybackError("Invalid EC-3 configuration size")
                        stream.seek(body)
                        found.append(parse_dec3(stream.read(finish - body)))
                start = finish

        # Non-ISO sources have no dec3; their generated init must still be checked.
        stream.seek(4)
        if stream.read(4) not in {b"ftyp", b"styp", b"moov", b"free", b"wide"}:
            return {}
        boxes(0, size, (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd", b"ec-3", b"dec3"))
    if len(found) > 1:
        raise PlaybackError("Ambiguous EC-3 sample entry configuration")
    return found[0] if found else {}


def _shift_chunk_offsets(body, width, shift, size, moov_start, moov_end):
    if len(body) < 8 or len(body) != 8 + struct.unpack_from(">I", body, 4)[0] * width:
        raise PlaybackError("Invalid MP4 chunk offset table")
    adjusted = bytearray(body[:8])
    for cursor in range(8, len(body), width):
        offset = int.from_bytes(body[cursor:cursor + width], "big")
        if offset >= size or moov_start <= offset < moov_end:
            raise PlaybackError("Invalid MP4 chunk offset")
        value = offset + (shift if offset >= moov_end else 0)
        if not 0 <= value < 1 << (width * 8):
            raise PlaybackError("MP4 chunk offset overflow")
        adjusted.extend(value.to_bytes(width, "big"))
    return bytes(adjusted)


def preserve_ec3_configuration(path: Path, reference, *, check_active=lambda: None):
    """Repair only a newly muxed scratch MP4/init using verified source signaling.

    FFmpeg < 8 drops the two JOC extension bytes while copying the audio packets.
    No encoded bytes change. A new sibling file is validated before replacement.
    Callers must never pass an already published asset as the destination.
    """
    if not reference or reference.get("joc") is not True:
        return False
    complexity = reference.get("complexity_index_type_a")
    if type(complexity) is not int or not 1 <= complexity <= 255:
        raise PlaybackError("Invalid source EC-3 JOC complexity")
    current = ec3_configuration(path)
    if not current or current["substreams"] != reference.get("substreams"):
        raise PlaybackError("EC-3 substreams changed during muxing")
    if current["joc"]:
        if current != reference:
            raise PlaybackError("EC-3 JOC configuration changed during muxing")
        return False
    check_active()
    size, moov = path.stat().st_size, None
    with path.open("rb") as stream:
        position = 0
        while position < size:
            if size - position < 8:
                raise PlaybackError("Truncated scratch MP4 box")
            stream.seek(position)
            length, kind = struct.unpack(">I4s", stream.read(8))
            header = 8
            if length == 1:
                if size - position < 16:
                    raise PlaybackError("Truncated scratch MP4 box")
                length, header = struct.unpack(">Q", stream.read(8))[0], 16
            if length < header or position + length > size or kind not in {b"ftyp", b"free", b"wide", b"mdat", b"moov"}:
                raise PlaybackError("Unsupported scratch MP4 structure for EC-3 repair")
            if kind == b"moov":
                if moov or header != 8 or length > 16 * 1024**2:
                    raise PlaybackError("Ambiguous or oversized scratch MP4 initialization")
                stream.seek(position)
                moov = (position, position + length, stream.read(length))
            position += length
    if not moov:
        raise PlaybackError("Scratch MP4 initialization missing")
    moov_start, moov_end, original = moov

    def rewrite(data, shift):
        changed = 0
        count = 4096

        def children(data, depth=0):
            nonlocal changed, count
            result = bytearray()
            position = 0
            while position < len(data):
                count -= 1
                if count < 0 or depth > 10 or len(data) - position < 8:
                    raise PlaybackError("Invalid scratch MP4 box structure")
                length, kind = struct.unpack_from(">I4s", data, position)
                if length < 8 or position + length > len(data):
                    raise PlaybackError("Invalid scratch MP4 box bounds")
                body = data[position + 8:position + length]
                if kind in {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd", b"ec-3"}:
                    skip = 8 if kind == b"stsd" else 28 if kind == b"ec-3" else 0
                    if len(body) < skip or (kind == b"stsd" and struct.unpack_from(">I", body, 4)[0] != 1):
                        raise PlaybackError("Ambiguous scratch MP4 sample entries")
                    body = body[:skip] + children(body[skip:], depth + 1)
                elif kind == b"dec3":
                    changed += 1
                    if parse_dec3(body) != current:
                        raise PlaybackError("Ambiguous scratch EC-3 configuration")
                    base_size = 2 + sum(4 if item[6] else 3 for item in current["substreams"])
                    body = body[:base_size] + bytes([1, complexity])
                elif kind in {b"stco", b"co64"}:
                    width = 4 if kind == b"stco" else 8
                    body = _shift_chunk_offsets(body, width, shift, size, moov_start, moov_end)
                elif kind in {b"mp4a", b"ac-3", b"Opus", b"enca"}:
                    raise PlaybackError("Additional audio sample entry cannot be repaired")
                result.extend(struct.pack(">I4s", len(body) + 8, kind) + body)
                position += length
            return bytes(result)

        result = children(data)
        if changed != 1:
            raise PlaybackError("Expected one EC-3 configuration in scratch MP4")
        return result

    updated = rewrite(original, 0)
    updated = rewrite(original, len(updated) - len(original))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".ec3-", dir=path.parent, delete=False) as output, path.open("rb") as source:
            temporary = Path(output.name)
            def copy_bytes(length):
                while length:
                    check_active()
                    chunk = source.read(min(length, 1024**2))
                    if not chunk:
                        raise PlaybackError("Scratch MP4 changed while preserving EC-3")
                    output.write(chunk)
                    length -= len(chunk)
            copy_bytes(moov_start)
            output.write(updated)
            source.seek(moov_end)
            copy_bytes(size - moov_end)
        if ec3_configuration(temporary) != reference:
            raise PlaybackError("EC-3 repaired configuration did not match the source")
        check_active()
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True
