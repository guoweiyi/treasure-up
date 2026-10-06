import test from 'node:test';
import assert from 'node:assert/strict';
import { contentBadges } from './contentBadges.ts';

test('only explicit archived/source flags create badges, never browser or codec guesses', () => {
  assert.deepEqual(contentBadges(), []);
  assert.deepEqual(
    contentBadges({ dolby_vision: false, dolby_atmos: 'false', charging_exclusive: null }),
    [],
  );
  assert.deepEqual(
    contentBadges({ dolby_vision: true }).map((item) => item.kind),
    ['vision'],
  );
});
test('the same compact labels distinguish archive content from actual Atmos output', () => {
  const badges = contentBadges({ charging_exclusive: true, dolby_vision: true, dolby_atmos: true });
  assert.deepEqual(
    badges.map((item) => item.text),
    ['充电专属', 'Dolby Vision', 'Dolby Atmos'],
  );
  assert.match(badges[1].title, /归档/);
  assert.match(badges[2].title, /不代表当前设备正在输出/);
});

test('only an explicit source-unavailable flag creates the archive-preserving badge', () => {
  assert.deepEqual(contentBadges({ source_unavailable: false }), []);
  assert.deepEqual(contentBadges({ source_unavailable: 'true' }), []);
  const [badge] = contentBadges({ source_unavailable: true });
  assert.equal(badge.text, '源站已失效');
  assert.match(badge.title, /本地归档保留/);
  assert.match(badge.title, /不表示已确认删除原因/);
});
