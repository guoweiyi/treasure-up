"""Bound request bodies before JSON parsing, including streamed/chunked bodies."""
from starlette.responses import JSONResponse


class RequestBodyLimit:
    def __init__(self, app, max_bytes=1_000_000):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        oversized = JSONResponse({"detail": "请求内容过大"}, status_code=413)
        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        return await oversized(scope, receive, send)
                except ValueError:
                    return await JSONResponse({"detail": "请求长度无效"}, status_code=400)(scope, receive, send)
        buffer = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(buffer) + len(chunk) > self.max_bytes:
                return await oversized(scope, receive, send)
            buffer.extend(chunk)
            if not message.get("more_body", False):
                break
        body = bytes(buffer)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
