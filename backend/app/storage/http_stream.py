"""Bounded file-like reading over streamed HTTP responses."""
class ResponseReader:
    def __init__(self, response):
        self._iterator = response.iter_bytes(chunk_size=1024 * 1024)
        self._pending = b""
        self._done = False

    def read(self, size=-1):
        if size == 0:
            return b""
        if size < 0:
            raise ValueError("Cloud streams require a bounded read size")
        while len(self._pending) < size and not self._done:
            block = next(self._iterator, None)
            if block is None:
                self._done = True
            else:
                self._pending += block
        value, self._pending = self._pending[:size], self._pending[size:]
        return value
