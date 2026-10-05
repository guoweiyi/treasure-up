import asyncio

from app.request_limits import RequestBodyLimit


def exercise(chunks, headers=(), maximum=10):
    sent, received = [], []
    pending = list(chunks)

    async def receive():
        received.append(True)
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        message = await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": message.get("body", b"")})

    asyncio.run(RequestBodyLimit(app, maximum)({"type": "http", "headers": headers}, receive, send))
    return sent, len(received)


def test_content_length_rejects_before_reading_body():
    result, reads = exercise([], [(b"content-length", b"11")])
    assert result[0]["status"] == 413 and reads == 0


def test_chunked_body_cannot_bypass_limit_and_stops_reading_immediately():
    result, reads = exercise([
        {"type": "http.request", "body": b"123456", "more_body": True},
        {"type": "http.request", "body": b"123456", "more_body": True},
        {"type": "http.request", "body": b"ignored", "more_body": False},
    ])
    assert result[0]["status"] == 413 and reads == 2


def test_incorrect_small_length_does_not_bypass_limit():
    result, _ = exercise([{"type": "http.request", "body": b"a" * 11}], [(b"content-length", b"2")])
    assert result[0]["status"] == 413


def test_exact_limit_preserves_utf8_and_all_streamed_bytes():
    body = "你好1234".encode()
    result, _ = exercise([
        {"type": "http.request", "body": body[:4], "more_body": True},
        {"type": "http.request", "body": body[4:]},
    ])
    assert result[0]["status"] == 200 and result[1]["body"] == body


def test_disconnect_does_not_enter_application():
    result, _ = exercise([{"type": "http.disconnect"}])
    assert result == []
