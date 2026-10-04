import test from 'node:test';
import assert from 'node:assert/strict';
import { api, ApiError, loadSession, session, sessionRevision } from '../api.ts';

const user = (id) => ({ id, username: `test-${id}`, role: 'viewer' });
function identity(id, csrf = `fixture-${id}`) {
  session.user = id ? user(id) : null;
  session.csrf = id ? csrf : '';
  session.ready = true;
}
const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

test('a late unauthorized response cannot clear a newly established login', async (t) => {
  identity('old');
  let respond;
  t.mock.method(
    globalThis,
    'fetch',
    () =>
      new Promise((resolve) => {
        respond = resolve;
      }),
  );
  const pending = api('/admin/jobs');
  identity('new');
  respond(json({ detail: 'old session expired' }, 401));
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(session.user.id, 'new');
  assert.equal(session.csrf, 'fixture-new');
});

test('private results from before logout are discarded and API caching is disabled', async (t) => {
  identity('old');
  let respond;
  t.mock.method(globalThis, 'fetch', (_url, options) => {
    assert.equal(options.cache, 'no-store');
    assert.equal(options.credentials, 'same-origin');
    return new Promise((resolve) => {
      respond = resolve;
    });
  });
  const pending = api('/videos?starred=true');
  const before = sessionRevision.value;
  identity(null);
  assert.ok(sessionRevision.value > before);
  respond(json({ items: [{ id: 'private-star' }] }));
  await assert.rejects(pending, { name: 'AbortError' });
});

test('a renewed session for the same user also invalidates older requests', async (t) => {
  identity('same', 'old-csrf');
  let respond;
  t.mock.method(
    globalThis,
    'fetch',
    () =>
      new Promise((resolve) => {
        respond = resolve;
      }),
  );
  const pending = api('/auth/me');
  identity('same', 'new-csrf');
  respond(json({ detail: 'expired' }, 401));
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(session.csrf, 'new-csrf');
});

test('a current unauthorized response still clears the current session', async (t) => {
  identity('current');
  t.mock.method(globalThis, 'fetch', async () => json({ detail: 'expired' }, 401));
  await assert.rejects(
    api('/admin/jobs'),
    (error) => error instanceof ApiError && error.status === 401,
  );
  assert.equal(session.user, null);
  assert.equal(session.csrf, '');
});

test('temporary startup failure does not permanently mark authentication ready', async (t) => {
  identity(null);
  session.ready = false;
  let authCalls = 0;
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (url.endsWith('/auth/me')) {
      authCalls++;
      return authCalls === 1
        ? json({ detail: 'temporary outage' }, 503)
        : json({ user: user('recovered'), csrf_token: 'fixture-recovered' });
    }
    return json({ site_name: 'Test', default_danmaku: true });
  });
  await assert.rejects(loadSession(), (error) => error.status === 503);
  assert.equal(session.ready, false);
  await loadSession();
  assert.equal(session.user.id, 'recovered');
  assert.equal(session.ready, true);
});
