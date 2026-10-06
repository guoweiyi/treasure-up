import test from 'node:test';
import assert from 'node:assert/strict';
import { requiresLogin, loginDestination } from './accessPolicy.ts';

test('private libraries require login even on direct video and creator links', () => {
  for (const path of ['/', '/videos/video', '/creators/up', '/collections']) {
    assert.equal(requiresLogin(path, false, false), true);
    assert.equal(requiresLogin(path, false, true), false);
  }
  assert.equal(requiresLogin('/saved', true, true), true);
  assert.equal(requiresLogin('/login', false, false), false);
});

test('login return paths cannot leave the website or create a login loop', () => {
  for (const path of [
    'https://evil.test',
    '//evil.test',
    '/\\evil.test',
    '/\nevil.test',
    '/login',
    '/a/../login',
    ['/videos/v'],
  ])
    assert.equal(loginDestination(path), '/');
  assert.equal(loginDestination('/videos/v?part=2#comments'), '/videos/v?part=2#comments');
});
