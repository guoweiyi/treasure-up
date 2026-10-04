"""Shared source rejection backoff and separate bulk-video pacing.

Ordinary API requests have no artificial account-wide interval. Rejections use
a short independent transaction; video slots are reserved by the download lane.
"""
import math
import random
import time
from urllib.parse import urlsplit

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models import SourceAccount
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
        # next_request_at and request_interval_seconds are legacy fields. Do
        # not turn an old configured delay into blocking foreground API work.

    def before_video(self, *, defer=False):
        self.check_cooldown()
        remaining = (utc(self.account.next_video_at) - self.clock()).total_seconds() if self.account.next_video_at else 0
        if defer and remaining > 2:
            raise IngestDeferred("等待同账号下一次媒体下载时段", code="video_interval", retry_after_seconds=math.ceil(remaining))
        self._wait_until(self.account.next_video_at)
        interval = max(10, min(3600, float(self.policy.get("video_interval_seconds", 60))))
        self.account.next_video_at = self.clock() + timedelta(seconds=interval + self._jitter())
        self.db.commit()

    def failed(self, error):
        if error.code != "rate_limited":
            return
        # A JSON rejection is decoded after releasing the HTTP/advisory scope.
        # Persist it independently, even if another request now holds that lock
        # or the caller loses its job lease. Never hold this short account row
        # lock while committing capture rows or acquiring the caller's job fence.
        with Session(self.db.get_bind()) as risk_db:
            account = risk_db.scalar(select(SourceAccount).where(SourceAccount.id == self.account.id)
                .with_for_update(key_share=True))
            if account is None:
                return
            account.risk_failures = min(20, (account.risk_failures or 0) + 1)
            baseline = max(60, min(86400, float(self.policy.get("risk_cooldown_seconds", 900))))
            delay = max(float(error.retry_after_seconds or 0), min(86400, baseline * 2 ** (account.risk_failures - 1)) + self._jitter())
            deadline = self.clock() + timedelta(seconds=delay)
            deadline = account.cooldown_until = max(utc(account.cooldown_until) or deadline, deadline)
            risk_db.commit()
        self.db.refresh(self.account, attribute_names=["cooldown_until", "risk_failures"])
        error.retry_after_seconds = math.ceil((deadline - self.clock()).total_seconds())
        # A real source rejection counts as an attempt. Later jobs waiting for
        # this persisted deadline use IngestDeferred and consume no attempts.
        error.blocked = False

    def succeeded(self):
        # A different lane may have encountered a challenge while this job
        # finished already downloaded/local work. Never clear its active risk.
        self.db.execute(update(SourceAccount).where(SourceAccount.id == self.account.id,
            or_(SourceAccount.cooldown_until.is_(None), SourceAccount.cooldown_until <= self.clock()))
            .values(risk_failures=0).execution_options(synchronize_session=False))
