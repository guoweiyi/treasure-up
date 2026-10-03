import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { validateOrigin } from '../src/policy.js';

test('only explicit HTTPS and loopback development HTTP roots are accepted', () => {
  for (const input of ['https://example.com', 'https://example.com:8443/', 'http://localhost:8788/', 'http://127.0.0.1:8788', 'http://[::1]:8788']) {
    assert.equal(validateOrigin(input), new URL(input).origin);
  }
  for (const input of ['http://server.lan', 'http://192.168.1.5', 'http://localhost.evil.invalid', 'file:///tmp/a', 'data:text/html,a', 'javascript:alert(1)', 'https://user:password@example.com', 'https://@example.com', 'https://example.com/path', 'https://example.com?', 'https://example.com#', 'https://example.com\\@evil.com', 'http://localhost:99999', 'https://tauri.localhost', 'https://ipc.localhost', 'https://treasure-up.invalid']) {
    assert.throws(() => validateOrigin(input), { name: 'Error' }, input);
  }
});

test('release capabilities expose only local connection commands', async () => {
  const capability = JSON.parse(await readFile(new URL('../src-tauri/capabilities/launcher.json', import.meta.url)));
  assert.equal(capability.local, true);
  assert.equal(capability.remote, undefined);
  assert.deepEqual(capability.windows, ['main']);
  assert.deepEqual(capability.permissions, ['allow-connection']);
  const config = JSON.parse(await readFile(new URL('../src-tauri/tauri.conf.json', import.meta.url)));
  assert.deepEqual(config.app.security.capabilities, ['launcher']);
  assert.equal(config.app.security.assetProtocol.enable, false);
  assert.match(config.app.security.csp, /frame-src 'none'/);
  const build = await readFile(new URL('../src-tauri/build.rs', import.meta.url), 'utf8');
  assert.match(build, /AppManifest::new\(\).commands/);
  const page = await readFile(new URL('../src/app.js', import.meta.url), 'utf8');
  assert.doesNotMatch(page, /innerHTML|insertAdjacentHTML|localStorage|sessionStorage/);
});
