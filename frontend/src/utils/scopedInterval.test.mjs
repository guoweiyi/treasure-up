import test from 'node:test';
import assert from 'node:assert/strict';
import { createScopedInterval } from './scopedInterval.ts';

test('a late async setup cannot restart polling after unmount', async (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  let cleanup;
  let calls = 0;
  let finishLoad;
  const load = new Promise((resolve) => {
    finishLoad = resolve;
  });
  const start = createScopedInterval((dispose) => {
    cleanup = dispose;
  });
  const setup = load.then(() => start(() => calls++, 1000));
  cleanup();
  finishLoad();
  await setup;
  t.mock.timers.tick(5000);
  assert.equal(calls, 0);
});

test('replacing a watcher discards its late poller and cleans up the current one', async (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  const cleanups = [];
  const scope = () => createScopedInterval((dispose) => cleanups.push(dispose));
  const old = scope();
  cleanups[0]();
  const current = scope();
  let obsoleteCalls = 0;
  let currentCalls = 0;
  current(() => currentCalls++, 1000);
  await Promise.resolve();
  old(() => obsoleteCalls++, 1000);
  t.mock.timers.tick(1000);
  assert.equal(obsoleteCalls, 0);
  assert.equal(currentCalls, 1);
  cleanups[1]();
  t.mock.timers.tick(1000);
  assert.equal(currentCalls, 1);
});

test('starting twice in the same active scope keeps a single timer', (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  let cleanup;
  let obsoleteCalls = 0;
  let currentCalls = 0;
  const start = createScopedInterval((dispose) => {
    cleanup = dispose;
  });
  start(() => obsoleteCalls++, 1000);
  start(() => currentCalls++, 1000);
  t.mock.timers.tick(1000);
  assert.equal(obsoleteCalls, 0);
  assert.equal(currentCalls, 1);
  cleanup();
});
