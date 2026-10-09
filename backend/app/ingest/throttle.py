"""Account-wide source pacing shared by HTTP workers and foreground pickers.

Only short transactions reserve request slots; no account row lock is held
while sleeping, running a job guard, or transferring source content.
"""
import math
import random
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models import Setting, SourceAccount
from .errors import IngestDeferred, IngestError

REQUEST_INTERVAL_MIN = 8
VIDEO_INTERVAL_MIN = 180
RISK_COOLDOWN_MIN = 1800
RISK_QUIET_PERIOD = timedelta(hours=24)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


class AccountPacer:
    def __init__(self, db, account, policy, guard, *, clock=None, sleep=time.sleep,
                 jitter=None, max_wait_seconds=None):
        self.db, self.account, self.policy, self.guard = db, account, policy, guard
        self.engine, self.account_id = db.get_bind(), account.id
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.jitter = jitter or (lambda maximum: random.uniform(0, maximum))
        self.max_wait_seconds = max_wait_seconds

    def _policy(self, reader):
        current = reader.get(Setting, "ingest")
        return current.value if current and isinstance(current.value, dict) else {}

    def _interval(self, current, name, minimum, maximum):
        # Jobs carry policy snapshots. Raising the live setting must also slow
        # already queued work; a per-source policy can only make it gentler.
        values = [minimum]
        for policy in (self.policy, current):
            try:
                value = float(policy.get(name, minimum))
                if math.isfinite(value):
                    values.append(value)
            except (ValueError, TypeError):
                pass
        return min(maximum, max(values))

    def _jitter(self, current):
        maximum = self._interval(current, "interval_jitter_seconds", 0, 300)
        return max(0, min(maximum, self.jitter(maximum)))

    def _cooldown(self, account):
        deadline = utc(account.cooldown_until)
        remaining = (deadline - self.clock()).total_seconds() if deadline else 0
        if remaining > 0:
            raise IngestDeferred("账号仍在源站冷却期，任务将延后", code="account_cooldown",
                                 retry_after_seconds=math.ceil(remaining))

    def _account(self, reader, *, lock=False):
        query = select(SourceAccount).where(SourceAccount.id == self.account_id)
        if lock:
            # NO KEY UPDATE permits unrelated capture rows to reference the
            # account while serializing all request/risk timestamp writers.
            query = query.with_for_update(key_share=True)
        account = reader.scalar(query)
        if account is None:
            raise IngestError("采集账号已不存在", code="login_required", retryable=False)
        return account

    def check_cooldown(self):
        self.guard()
        # The caller's ORM object may predate a rejection in another process.
        # A dedicated session is also safe for yt-dlp fragment callback threads.
        with Session(self.engine) as reader:
            self._cooldown(self._account(reader))

    def _reserve(self, field, interval_name, minimum, maximum, *, defer=False):
        started = self.clock()
        while True:
            self.guard()
            with Session(self.engine) as writer:
                current = self._policy(writer)
                account = self._account(writer, lock=True)
                self._cooldown(account)
                now = self.clock()
                deadline = utc(getattr(account, field))
                remaining = (deadline - now).total_seconds() if deadline else 0
                if remaining <= 0:
                    interval = self._interval(current, interval_name, minimum, maximum)
                    setattr(account, field, now + timedelta(seconds=interval + self._jitter(current)))
                    writer.commit()
                    return
            # Release the account row before any job fencing/commit or sleep.
            # Waiters do not book future slots, so a canceled job leaves no
            # reservation behind and cooldown recovery cannot release a burst.
            if defer or (self.max_wait_seconds is not None
                         and (now - started).total_seconds() + remaining > self.max_wait_seconds):
                code = "video_interval" if field == "next_video_at" else "request_interval"
                message = "等待下一次媒体下载时段" if field == "next_video_at" else "账号请求正在排队，请稍后重试"
                raise IngestDeferred(message, code=code, retry_after_seconds=math.ceil(remaining))
            self.sleep(min(0.5, remaining))

    def before_request(self, url=None):
        host = (urlsplit(url).hostname or "").lower() if url else "bilibili.com"
        if host != "bilibili.com" and not host.endswith(".bilibili.com"):
            # Public CDN images/media do not use the API interval, but a new
            # rejection must still stop the next fragment in an active job.
            self.check_cooldown()
            return
        self._reserve("next_request_at", "request_interval_seconds", REQUEST_INTERVAL_MIN, 120)

    def before_video(self, *, defer=False):
        self._reserve("next_video_at", "video_interval_seconds", VIDEO_INTERVAL_MIN, 3600, defer=defer)

    def after_video(self):
        # Space videos from the end of an attempt, including slow downloads.
        # This must also run after cancellation, so it does not call the guard.
        with Session(self.engine) as writer:
            current = self._policy(writer)
            account = self._account(writer, lock=True)
            interval = self._interval(current, "video_interval_seconds", VIDEO_INTERVAL_MIN, 3600)
            deadline = self.clock() + timedelta(seconds=interval + self._jitter(current))
            account.next_video_at = max(utc(account.next_video_at) or deadline, deadline)
            writer.commit()

    def failed(self, error):
        if error.code != "rate_limited" or getattr(error, "account_backoff_applied", False):
            return
        with Session(self.engine) as writer:
            current = self._policy(writer)
            account = self._account(writer, lock=True)
            now = self.clock()
            existing = utc(account.cooldown_until)
            # Several requests already in flight may report the same episode.
            # They must not each double an active cooldown or keep extending it.
            if existing and existing > now:
                deadline = existing
            else:
                if existing and existing + RISK_QUIET_PERIOD <= now:
                    account.risk_failures = 0
                account.risk_failures = min(20, (account.risk_failures or 0) + 1)
                baseline = self._interval(current, "risk_cooldown_seconds", RISK_COOLDOWN_MIN, 86400)
                delay = min(86400, baseline * 2 ** (account.risk_failures - 1)) + self._jitter(current)
                deadline = now + timedelta(seconds=delay)
            deadline = max(deadline, now + timedelta(seconds=max(0, float(error.retry_after_seconds or 0))))
            account.cooldown_until = deadline
            writer.commit()
        error.retry_after_seconds = math.ceil((deadline - self.clock()).total_seconds())
        error.account_backoff_applied = True
        # Only the actual rejection consumes an attempt. Other jobs share this
        # deadline through IngestDeferred without repeatedly hitting the source.
        error.blocked = False

    def succeeded(self):
        # A successful local/small job does not erase a recent source rejection.
        # Reset escalation only after a whole day beyond the previous cooldown.
        with Session(self.engine) as writer:
            writer.execute(update(SourceAccount).where(SourceAccount.id == self.account_id,
                or_(SourceAccount.cooldown_until.is_(None),
                    SourceAccount.cooldown_until <= self.clock() - RISK_QUIET_PERIOD))
                .values(risk_failures=0).execution_options(synchronize_session=False))
            writer.commit()
