"""Small, strict reader for DmSegMobileReply / DanmakuElem.

Unknown fields are skipped; malformed bytes must never mean an empty segment.
Reference: bilibili/community/service/dm/v1/dm.proto (community schema).
"""
from .errors import IngestError


def _fail():
    raise IngestError("弹幕分段格式损坏或不受支持", code="invalid_danmaku")


def _varint(data, pos):
    result = 0
    for i in range(10):
        if pos >= len(data):
            _fail()
        byte = data[pos]
        pos += 1
        if i == 9 and byte > 1:
            _fail()
        result |= (byte & 127) << (7 * i)
        if byte < 128:
            return result, pos
    _fail()


def _fields(data):
    pos = 0
    while pos < len(data):
        tag, pos = _varint(data, pos)
        field, wire = tag >> 3, tag & 7
        if field == 0 or field > 536870911:
            _fail()
        if wire == 0:
            value, pos = _varint(data, pos)
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if pos + size > len(data):
                _fail()
            value, pos = data[pos:pos + size], pos + size
        elif wire == 2:
            size, pos = _varint(data, pos)
            if size > len(data) - pos:
                _fail()
            value, pos = data[pos:pos + size], pos + size
        else:
            _fail()
        yield field, wire, value


def decode_danmaku(data: bytes):
    if len(data) > 32 * 1024 * 1024:
        _fail()
    results = []
    for field, wire, value in _fields(data):
        if field == 2:
            if wire != 0:
                _fail()
            if value == 1:
                raise IngestError("该分P的弹幕已关闭", code="danmaku_closed", retryable=False)
        if field != 1:
            continue
        if wire != 2:
            _fail()
        item = {"id": "0", "time": 0, "mode": 1, "size": 25, "color": "#ffffff", "text": ""}
        present = set()
        for key, kind, val in _fields(value):
            if key in (1, 2, 3, 4, 5, 8, 9, 11, 13):
                if kind != 0:
                    _fail()
                if key == 1:
                    item["id"] = str(val)
                elif key == 2:
                    if val > 2147483647:
                        _fail()
                    item["time"] = val / 1000
                elif key == 3:
                    item["mode"] = val
                elif key == 4:
                    item["size"] = val
                elif key == 5:
                    if val > 0xFFFFFF:
                        _fail()
                    item["color"] = f"#{val:06x}"
                elif key == 8:
                    item["ctime"] = val
                elif key == 11:
                    item["pool"] = val
            elif key in (6, 7, 12):
                if kind != 2:
                    _fail()
                try:
                    decoded = val.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    _fail()
                if key == 7:
                    item["text"] = decoded
                elif key == 6:
                    item["midHash"] = decoded
                elif decoded:
                    if not decoded.isdecimal():
                        _fail()
                    item["id"] = decoded
            present.add(key)
        if not ({1, 12} & present) or 7 not in present:
            _fail()
        results.append(item)
        if len(results) > 100000:
            _fail()
    return results
