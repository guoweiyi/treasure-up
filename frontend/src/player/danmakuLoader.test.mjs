import test from 'node:test';
import assert from 'node:assert/strict';
import { loadDanmaku, normalizeDanmaku } from './danmakuLoader.ts';

const row = (text, time = 3, mode = 0, color = 0xffffff) => ({ text, time, mode, color });
test('deferred danmaku can attach after player startup without holding it or losing text', async () => {
  let resolve, rows;
  const task = loadDanmaku(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
    () => true,
    (value) => {
      rows = value;
    },
    () => assert.fail('valid danmaku'),
  );
  assert.equal(rows, undefined);
  // Media setup remains independent while this optional request is outstanding.
  resolve([row('迟到的弹幕')]);
  await task;
  assert.deepEqual(rows, [row('迟到的弹幕', 3, 0, '#ffffff')]);
});
test('a switched part ignores both late danmaku and late errors', async () => {
  for (const failed of [false, true]) {
    let complete,
      current = true;
    const task = loadDanmaku(
      () =>
        new Promise((resolve, reject) => {
          complete = failed ? reject : resolve;
        }),
      () => current,
      () => assert.fail('old part applied'),
      () => assert.fail('old part error'),
    );
    current = false;
    complete(failed ? new Error('old request') : [row('旧弹幕')]);
    await task;
  }
});
test('current danmaku failures are contained and invalid formats are filtered', async () => {
  const error = new Error('optional resource unavailable');
  let reported;
  await loadDanmaku(
    async () => {
      throw error;
    },
    () => true,
    () => assert.fail('no rows'),
    (value) => {
      reported = value;
    },
  );
  assert.equal(reported, error);
  assert.deepEqual(
    normalizeDanmaku([
      row('bad time', NaN),
      row('unsupported', 2, 7),
      row('red', 1, 1, 0xff0000),
      row('literal', 2, 2, 'url(secret)'),
      row('css', 3, 0, '#a1b'),
    ]).map(({ text, color }) => ({ text, color })),
    [
      { text: 'red', color: '#ff0000' },
      { text: 'literal', color: '#ffffff' },
      { text: 'css', color: '#a1b' },
    ],
  );
});
