"""Persisted, account-serialized pacing and source-request backoff.

Jitter spreads scheduled load; it never changes identity or evades challenges.
The runner must hold the account advisory lock while using this object.
"""
import math
import random
import time
from datetime import datetime, timedelta, timezone

from .errors import IngestDeferred


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


class AccountPacer:
    def __init__(self, db, account, policy, guard, *, clock=None, sleep=time.sleep, jitter=None):
        self.db, self.account, self.policy, self.guard = db, account, policy, guard
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.jitter = jitter or (lambda maximum: random.uniform(0, maximum))

    def _jitter(self):
        return self.jitter(max(0, min(300, float(self.policy.get("interval_jitter_seconds", 10)))))

    def check_cooldown(self):
        self.guard()
        remaining = (utc(self.account.cooldown_until) - self.clock()).total_seconds() if self.account.cooldown_until else 0
        if remaining > 0:
            raise IngestDeferred("账号仍在源站冷却期，任务将延后", code="account_cooldown",
                              retry_after_seconds=math.ceil(remaining))

    def _wait_until(self, deadline):
        while deadline:
            self.guard()
            remaining = (utc(deadline) - self.clock()).total_seconds()
            if remaining <= 0:
                return
            self.sleep(min(0.5, remaining))

    def before_request(self, url=None):
        self.check_cooldown()
        self._wait_until(self.account.next_request_at)
        interval = max(1, min(120, float(self.policy.get("request_interval_seconds", 3))))
        self.account.next_request_at = self.clock() + timedelta(seconds=interval + self._jitter())
        self.db.commit()

    def before_video(self):
        self.check_cooldown()
        self._wait_until(self.account.next_video_at)
        interval = max(10, min(3600, float(self.policy.get("video_interval_seconds", 60))))
        self.account.next_video_at = self.clock() + timedelta(seconds=interval + self._jitter())
        self.db.commit()

    def failed(self, error):
        if error.code != "rate_limited":
            return
        self.account.risk_failures = min(20, (self.account.risk_failures or 0) + 1)
        baseline = max(60, min(86400, float(self.policy.get("risk_cooldown_seconds", 900))))
        delay = max(float(error.retry_after_seconds or 0), min(86400, baseline * 2 ** (self.account.risk_failures - 1)) + self._jitter())
        deadline = self.clock() + timedelta(seconds=delay)
        self.account.cooldown_until = max(utc(self.account.cooldown_until) or deadline, deadline)
        error.retry_after_seconds = math.ceil((utc(self.account.cooldown_until) - self.clock()).total_seconds())
        # A real source rejection counts as an attempt. Later jobs waiting for
        # this persisted deadline use IngestDeferred and consume no attempts.
        error.blocked = False
        self.db.commit()

    def succeeded(self):
        self.account.risk_failures = 0
