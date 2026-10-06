"""Build private fMP4 HLS fixtures from a shared MOV segmenting context.

The caller must still verify codecs, Dolby configuration and complete packet
digests before publishing. This module neither publishes assets nor changes DB
state. It preserves all media-fragment bytes and their global decode times.
"""
from __future__ import annotations

import csv
from fractions import Fraction
import math
import os
from pathlib import Path
import re
import stat
import struct
from uuid import uuid4

from .tools import PlaybackError

MAX_SEGMENTS = 100_000
MAX_BOXES = 1_000_000
MAX_INIT_BYTES = 64 * 1024**2
MAX_METADATA_BYTES = 2 * 1024**2
COPY_BYTES = 1024**2
_NAME = re.compile(r"segment_([0-9]{6})\.m4s\Z")


def fragment_movie_timescale(streams):
    """Represent both input time bases exactly in the movie edit-list clock."""
    scale = 1
    count = 0
    try:
        for stream in streams:
            count += 1
            base = Fraction(stream["time_base"])
            if base <= 0:
                raise ValueError()
            scale = math.lcm(scale, base.denominator)
            if scale > 2**31 - 1:
                raise ValueError()
        if not count:
            raise ValueError()
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        raise PlaybackError("Media time bases cannot be represented safely") from None
    return scale


def segment_mux_options(folder, *, segment_seconds, movie_timescale):
    """Append to the existing stream-copy/maps command; never reset timestamps."""
    if (type(segment_seconds) is not int or not 2 <= segment_seconds <= 30 or
            type(movie_timescale) is not int or not 1 <= movie_timescale <= 2**31 - 1):
        raise PlaybackError("Invalid fragmented media settings")
    folder = Path(folder)
    # A forced segment flush otherwise makes movenc infer the following first
    # DTS from a rounded nominal final packet duration (notably VFR video).
    # One-packet lookahead supplies the exact decode interval. Explicit PTS/DTS
    # expressions are essential: setts' default TS would erase B-frame CTS.
    duration = r"setts=pts=PTS:dts=DTS:duration=if(gt(NEXT_DTS\,DTS)\,NEXT_DTS-DTS\,DURATION)"
    return ["-bsf:v", duration, "-f", "segment", "-segment_time", str(segment_seconds), "-reference_stream", "v:0",
        "-reset_timestamps", "0", "-individual_header_trailer", "0", "-segment_format", "mp4",
        "-segment_format_options", "movflags=+frag_keyframe+delay_moov+default_base_moof+write_colr:"
        f"frag_duration=1000000:strict=unofficial:movie_timescale={movie_timescale}",
        "-segment_list", str(folder / "segments.csv"), "-segment_list_type", "csv",
        "-segment_list_size", "0", str(folder / "segment_%06d.m4s")]


def _open_regular(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise PlaybackError("Fragment files must be regular private files")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError:
        raise PlaybackError("Fragment file is unavailable") from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise PlaybackError("Fragment files must be regular private files")
    return os.fdopen(fd, "rb"), info


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _box_header(stream, limit):
    start = stream.tell()
    header = stream.read(8)
    if len(header) != 8:
        raise PlaybackError("Truncated fragmented MP4 box")
    size, kind = struct.unpack(">I4s", header)
    width = 8
    if size == 1:
        raw = stream.read(8)
        if len(raw) != 8:
            raise PlaybackError("Truncated extended MP4 box")
        size, width = struct.unpack(">Q", raw)[0], 16
    if size < width or start + size > limit:
        raise PlaybackError("Invalid fragmented MP4 box size")
    return kind, start, size, width


def _children(data):
    position = 0
    count = 0
    while position < len(data):
        count += 1
        if count > 10_000 or len(data) - position < 8:
            raise PlaybackError("Invalid MP4 metadata structure")
        size, kind = struct.unpack_from(">I4s", data, position)
        width = 8
        if size == 1:
            if len(data) - position < 16:
                raise PlaybackError("Truncated extended MP4 metadata box")
            size, width = struct.unpack_from(">Q", data, position + 8)[0], 16
        if size < width or position + size > len(data):
            raise PlaybackError("Invalid MP4 metadata box size")
        yield kind, memoryview(data)[position + width:position + size]
        position += size


def _inspect_moof(data, state):
    sequence, tracks = None, []
    for kind, content in _children(data):
        if kind == b"mfhd":
            if sequence is not None or len(content) != 8 or bytes(content[:4]) != b"\0" * 4:
                raise PlaybackError("Invalid movie-fragment sequence header")
            sequence = struct.unpack_from(">I", content, 4)[0]
        elif kind == b"traf":
            track, decode_time, runs = None, None, 0
            for child, body in _children(content):
                if child == b"tfhd":
                    if track is not None or len(body) < 8 or body[0] != 0:
                        raise PlaybackError("Invalid fragment track header")
                    flags = int.from_bytes(body[1:4], "big")
                    # Removing the init is safe only with a base relative to moof.
                    allowed = 0x020000 | 0x000002 | 0x000008 | 0x000010 | 0x000020
                    if flags & ~allowed or not flags & 0x020000:
                        raise PlaybackError("Fragment offsets are not relative to moof")
                    expected = 8 + 4 * sum(bool(flags & bit) for bit in (2, 8, 16, 32))
                    if len(body) != expected:
                        raise PlaybackError("Invalid fragment track header size")
                    track = struct.unpack_from(">I", body, 4)[0]
                    if not track:
                        raise PlaybackError("Invalid fragment track identifier")
                elif child == b"tfdt":
                    if decode_time is not None or len(body) not in (8, 12) or bytes(body[1:4]) != b"\0" * 3:
                        raise PlaybackError("Invalid fragment decode timestamp")
                    if body[0] not in (0, 1) or len(body) != (12 if body[0] else 8):
                        raise PlaybackError("Invalid fragment decode timestamp version")
                    decode_time = int.from_bytes(body[4:], "big")
                elif child == b"trun":
                    if len(body) < 8 or body[0] not in (0, 1):
                        raise PlaybackError("Invalid fragment sample table")
                    flags = int.from_bytes(body[1:4], "big")
                    count = struct.unpack_from(">I", body, 4)[0]
                    if flags & ~0x000F05 or not 1 <= count <= 1_000_000:
                        raise PlaybackError("Invalid fragment sample count")
                    expected = (8 + (4 if flags & 1 else 0) + (4 if flags & 4 else 0) +
                        count * 4 * sum(bool(flags & bit) for bit in (0x100, 0x200, 0x400, 0x800)))
                    if len(body) != expected:
                        raise PlaybackError("Invalid fragment sample table size")
                    runs += 1
                else:
                    raise PlaybackError("Unsupported fragment track metadata")
            if track is None or decode_time is None or not runs:
                raise PlaybackError("Incomplete fragment track metadata")
            if track in {t for t, _ in tracks}:
                raise PlaybackError("Duplicate fragment track")
            tracks.append((track, decode_time))
        else:
            raise PlaybackError("Unsupported movie-fragment metadata")
    if sequence != state["sequence"] + 1 or not tracks:
        raise PlaybackError("Movie fragments are missing or out of order")
    for track, value in tracks:
        if track in state["tracks"] and value <= state["tracks"][track]:
            raise PlaybackError("Fragment decode timestamps do not advance")
        state["tracks"][track] = value
    state["sequence"] = sequence


def _read_csv(folder, guard):
    path = folder / "segments.csv"
    stream, info = _open_regular(path)
    rows = []
    prior_start, prior_end = None, None
    with stream:
        if not 0 < info.st_size <= 32 * 1024**2:
            raise PlaybackError("Invalid fragment list size")
        while raw := stream.readline(513):
            guard()
            if len(raw) > 512 or len(rows) >= MAX_SEGMENTS:
                raise PlaybackError("Fragment list exceeds its limits")
            try:
                values = next(csv.reader([raw.decode("ascii")], strict=True))
                if len(values) != 3:
                    raise ValueError()
                name, start_text, end_text = values
                if not _NAME.fullmatch(name) or name != f"segment_{len(rows):06d}.m4s":
                    raise ValueError()
                if not all(re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,9})?", v) for v in (start_text, end_text)):
                    raise ValueError()
                start, end = Fraction(start_text), Fraction(end_text)
                if start < 0 or end > 7 * 86400 or not 0 < end - start <= 86400:
                    raise ValueError()
                if prior_start is not None:
                    # segment.c records max(packet PTS + packet duration) as
                    # the prior end, and the next keyframe PTS as this start.
                    # With rounded/VFR packet durations they can overlap or
                    # leave a sub-frame gap despite an intact decode timeline.
                    # Playlist boundaries follow consecutive keyframe starts;
                    # compressed packets and their tfdt remain untouched.
                    if not prior_start < start or not prior_end < end or start - prior_start > 86400:
                        raise ValueError()
            except (ValueError, UnicodeError, csv.Error, StopIteration):
                raise PlaybackError("Invalid local fragment list") from None
            if rows:
                rows[-1]["duration"] = float(start - prior_start)
            rows.append({"filename": name, "duration": float(end - start)})
            prior_start, prior_end = start, end
        if _identity(os.fstat(stream.fileno())) != _identity(info):
            raise PlaybackError("Fragment list changed while reading")
    if not rows:
        raise PlaybackError("Fragment list is empty")
    return rows


def _inspect_file(path, *, first, last, state, guard):
    source, info = _open_regular(path)
    init_end, media_start, media_end = 0, None, info.st_size
    expect_mdat, seen_media, stage = False, False, 0
    with source:
        while source.tell() < info.st_size:
            guard()
            state["boxes"] += 1
            if state["boxes"] > MAX_BOXES:
                raise PlaybackError("Fragment box count exceeds its limit")
            kind, start, size, width = _box_header(source, info.st_size)
            if first and stage < 2:
                if kind != (b"ftyp" if stage == 0 else b"moov") or start + size > MAX_INIT_BYTES:
                    raise PlaybackError("Invalid shared MP4 initialization")
                init_end = start + size
                stage += 1
            elif kind == b"moof" and not expect_mdat:
                if size > MAX_METADATA_BYTES:
                    raise PlaybackError("Fragment metadata exceeds its limit")
                content = source.read(size - width)
                if len(content) != size - width:
                    raise PlaybackError("Truncated fragment metadata")
                _inspect_moof(content, state)
                media_start = start if media_start is None else media_start
                expect_mdat, seen_media = True, True
            elif kind == b"mdat" and expect_mdat and size > width:
                expect_mdat = False
            elif kind == b"mfra" and last and seen_media and not expect_mdat and start + size == info.st_size:
                # This whole-file seek index uses offsets from before init
                # extraction. HLS seeks by its playlist and per-fragment tfdt.
                media_end = start
            else:
                raise PlaybackError("Unexpected fragmented MP4 structure")
            source.seek(start + size)
        if not seen_media or expect_mdat or (first and stage != 2):
            raise PlaybackError("Incomplete fragmented MP4 media")
        if _identity(os.fstat(source.fileno())) != _identity(info):
            raise PlaybackError("Fragment changed during inspection")
    return info, init_end, media_start, media_end


def _copy_range(source, target, start, length, guard):
    source.seek(start)
    while length:
        guard()
        block = source.read(min(COPY_BYTES, length))
        if not block:
            raise PlaybackError("Fragment was truncated during copying")
        target.write(block)
        length -= len(block)


def finalize_fragmented_hls(folder, *, check_active=None):
    """Validate private muxer output, split init without buffering a segment.

    The MOV context is shared across segments, so every moof retains its exact
    global tfdt. Only ftyp/moov and the obsolete final mfra change locations.
    Any later publication must run the existing full preservation verifiers.
    """
    guard = check_active or (lambda: None)
    folder = Path(folder)
    if not folder.is_dir() or folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        raise PlaybackError("A private fragment directory is required")
    folder = folder.resolve()
    if any((folder / name).exists() or (folder / name).is_symlink() for name in ("init.mp4", "index.m3u8")):
        raise PlaybackError("Fragment output already exists")
    rows = _read_csv(folder, guard)
    state = {"sequence": 0, "tracks": {}, "boxes": 0}
    inspections = []
    for index, row in enumerate(rows):
        inspections.append(_inspect_file(folder / row["filename"], first=index == 0,
            last=index == len(rows) - 1, state=state, guard=guard))
    for index, (row, inspection) in enumerate(zip(rows, inspections)):
        info, init_end, start, end = inspection
        if not init_end and start == 0 and end == info.st_size:
            continue
        guard()
        path = folder / row["filename"]
        source, current = _open_regular(path)
        temporary = folder / f".fragment-{uuid4().hex}"
        try:
            with source:
                if _identity(current) != _identity(info):
                    raise PlaybackError("Fragment changed before copying")
                if init_end:
                    with (folder / "init.mp4").open("xb") as init:
                        _copy_range(source, init, 0, init_end, guard)
                with temporary.open("xb") as target:
                    _copy_range(source, target, start, end - start, guard)
                if _identity(os.fstat(source.fileno())) != _identity(info) or _identity(path.lstat()) != _identity(info):
                    raise PlaybackError("Fragment changed during copying")
            guard()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    guard()
    target_duration = math.ceil(max(row["duration"] for row in rows))
    playlist = folder / "index.m3u8"
    with playlist.open("x", encoding="ascii", newline="\n") as output:
        output.write(f'#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:{target_duration}\n'
                     '#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-MAP:URI="init.mp4"\n')
        for row in rows:
            guard()
            output.write(f'#EXTINF:{row["duration"]:.9f},\n{row["filename"]}\n')
        output.write('#EXT-X-ENDLIST\n')
    return playlist
