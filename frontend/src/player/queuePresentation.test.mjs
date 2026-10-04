import { test } from 'node:test';
import assert from 'node:assert/strict';
import { queuePosition, queueMenuFocus } from './queuePresentation.ts';
test('queue position distinguishes BV order from part order without inventing an unloaded position', () => {
  assert.deepEqual(queuePosition(3, 20, 2, 4), { video: '4 / 20', part: 'P2 / 4' });
  assert.deepEqual(queuePosition(-1, 2000, 1, 2), { video: '— / 2000', part: 'P1 / 2' });
  assert.deepEqual(queuePosition(-1, 0, 1, 1), { video: '', part: '' });
});
test('mode menu keyboard wraps, reaches independent autoplay switch, and ignores unrelated keys', () => {
  assert.equal(queueMenuFocus('ArrowDown', 2, 4), 3);
  assert.equal(queueMenuFocus('ArrowDown', 3, 4), 0);
  assert.equal(queueMenuFocus('ArrowUp', 0, 4), 3);
  assert.equal(queueMenuFocus('Home', 3, 4), 0);
  assert.equal(queueMenuFocus('End', 0, 4), 3);
  assert.equal(queueMenuFocus('ArrowUp', -1, 4), 3);
  assert.equal(queueMenuFocus('Escape', 2, 4), null);
  assert.equal(queueMenuFocus('ArrowDown', 0, 0), null);
});
