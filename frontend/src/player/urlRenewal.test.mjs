import test from 'node:test';
import assert from 'node:assert/strict';
import { createUrlRenewal } from './urlRenewal.ts';
test('signed URL refresh waits for active playback and renews only once until replaced', () => {
  let now = 100000,
    active = false,
    calls = 0,
    scheduled;
  const controller = createUrlRenewal(
    () => active,
    () => calls++,
    {
      now: () => now,
      schedule: (run, delay) => {
        scheduled = { run, delay };
        return 1;
      },
      cancel: () => {
        scheduled = undefined;
      },
    },
  );
  controller.set(new Date(now + 3600000).toISOString());
  assert.equal(scheduled.delay, 3540000);
  now += 3540000;
  scheduled.run();
  assert.equal(calls, 0);
  active = true;
  controller.check();
  controller.check();
  assert.equal(calls, 1);
  controller.set(new Date(now + 3600000).toISOString());
  controller.check();
  assert.equal(calls, 1);
  controller.stop();
  now += 3600000;
  controller.check();
  assert.equal(calls, 1);
  assert.equal(scheduled, undefined);
});
test('public or invalid expiry never schedules a refresh', () => {
  let schedules = 0;
  const controller = createUrlRenewal(
    () => true,
    () => assert.fail('unexpected renewal'),
    {
      now: () => 0,
      schedule: () => {
        schedules++;
        return 1;
      },
      cancel: () => {},
    },
  );
  for (const expiry of [null, undefined, '', 'invalid']) {
    controller.set(expiry);
    controller.check();
  }
  assert.equal(schedules, 0);
});
