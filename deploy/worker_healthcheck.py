"""Check this container's Celery consumer, without accepting another worker's reply."""
import socket
import sys


def worker_ready(app, expected_queues, hostname=None):
    node = f"celery@{hostname or socket.gethostname()}"
    response = app.control.inspect(destination=[node], timeout=3).active_queues()
    queues = (response or {}).get(node)
    if not isinstance(queues, list):
        return False
    active = {queue.get("name") for queue in queues if isinstance(queue, dict)}
    return bool(expected_queues) and set(expected_queues) <= active


def main():
    try:
        # The health probe needs only the control client, not task registration
        # and its database/storage/media imports in every worker every interval.
        from celery import Celery
        from app.config import settings
        client = Celery("healthcheck", broker=settings.redis_url)
        return 0 if worker_ready(client, sys.argv[1:]) else 1
    except Exception:
        # Health logs must never include broker/database connection strings.
        print("Worker queue readiness check failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
