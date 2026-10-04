import test from 'node:test';
import assert from 'node:assert/strict';
import { createJobPoller } from './jobPoller.ts';
import { finishedJob, jobCounts, jobPhase } from './jobDisplay.ts';

test('polling is sequential and closing discards an in-flight response', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let finish;
  let calls = 0;
  const updates = [];
  const poller = createJobPoller(
    () => {
      calls++;
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
    (job) => updates.push(job),
    () => {},
    finishedJob,
  );
  poller.start('a');
  t.mock.timers.tick(30000);
  assert.equal(calls, 1);
  poller.stop();
  finish({ status: 'running' });
  await Promise.resolve();
  t.mock.timers.tick(30000);
  assert.equal(calls, 1);
  assert.deepEqual(updates, []);
});
test('completed jobs stop polling and switching jobs invalidates older completion', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const pending = new Map(),
    updates = [];
  const poller = createJobPoller(
    (id) => new Promise((resolve) => pending.set(id, resolve)),
    (job) => updates.push(job),
    () => {},
    finishedJob,
  );
  poller.start('a');
  poller.start('b');
  pending.get('a')({ id: 'a', status: 'succeeded' });
  pending.get('b')({ id: 'b', status: 'succeeded' });
  await Promise.resolve();
  t.mock.timers.tick(30000);
  assert.deepEqual(
    updates.map((job) => job.id),
    ['b'],
  );
});
test('job display reads real phase and counts without rendering raw checkpoints', () => {
  const job = {
    checkpoint: {
      progress: { phase: 'compatible_copy', completed_parts: 2, total_parts: 3 },
      signed_url: 'never-render',
    },
    result: {},
  };
  assert.equal(jobPhase(job), '生成兼容副本');
  assert.deepEqual(jobCounts(job), [['已完成', '2 / 3']]);
});
