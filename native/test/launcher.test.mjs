import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { validateOrigin } from '../src/policy.js';

const script = (await readFile(new URL('../src/app.js', import.meta.url), 'utf8'))
  .replace(/^import \{ validateOrigin \} from '\.\/policy\.js';\r?\n/, '');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const settle = () => new Promise(resolve => setImmediate(resolve));

function launcher(invoke, confirm = () => true) {
  const elements = Object.fromEntries(['connect', 'origin', 'status', 'submit', 'forget'].map(id => [id, {
    value: '', textContent: '', disabled: false, handlers: {}, attributes: {},
    addEventListener(type, listener) { this.handlers[type] = listener; },
    setAttribute(name, value) { this.attributes[name] = value; },
  }]));
  vm.runInNewContext(script, {
    document: { querySelector: selector => elements[selector.slice(1)] },
    window: invoke ? { __TAURI__: { core: { invoke } } } : {},
    validateOrigin, confirm,
  });
  return {
    ...elements,
    submitForm: () => elements.connect.handlers.submit({ preventDefault() {} }),
    clear: () => elements.forget.handlers.click(),
  };
}

test('initial settings cannot race with submit or clear and repopulate a forgotten server', async () => {
  const saved = deferred();
  const calls = [];
  const page = launcher((command, args) => {
    calls.push([command, args]);
    return command === 'load_connection' ? saved.promise : Promise.resolve();
  });
  assert.equal(page.origin.disabled, true);
  assert.equal(page.connect.attributes['aria-busy'], 'true');
  await page.submitForm();
  await page.clear();
  assert.deepEqual(calls.map(call => call[0]), ['load_connection']);
  saved.resolve({ origin: 'https://saved.example' });
  await settle();
  assert.equal(page.origin.value, 'https://saved.example');
  assert.equal(page.origin.disabled, false);
  await page.clear();
  assert.equal(page.origin.value, '');
  assert.equal(page.connect.attributes['aria-busy'], 'false');
  assert.deepEqual(calls.map(call => call[0]), ['load_connection', 'forget_connection']);
});

test('failed initialization stays editable, a failed probe can retry, and rapid operations stay exclusive', async () => {
  const probe = deferred();
  const calls = [];
  const page = launcher((command, args) => {
    calls.push([command, args]);
    if (command === 'load_connection') return Promise.reject('连接设置损坏，请清除后重新设置');
    return probe.promise;
  });
  await settle();
  assert.match(page.status.textContent, /损坏/);
  assert.equal(page.forget.disabled, false);
  page.origin.value = 'https://new.example/api/v1';
  const first = page.submitForm();
  await page.submitForm();
  await page.clear();
  assert.deepEqual(calls.map(call => call[0]), ['load_connection', 'connect_server']);
  assert.equal(calls[1][1].origin, 'https://new.example');
  probe.reject('无法连接服务器');
  await first;
  assert.match(page.status.textContent, /无法连接/);
  assert.equal(page.origin.value, 'https://new.example/api/v1');
  assert.equal(page.submit.disabled, false);
  await page.submitForm();
  assert.equal(calls.length, 3);
});

test('canceling clear never invokes native cleanup, and no bridge cannot submit', async () => {
  const calls = [];
  const page = launcher(command => { calls.push(command); return Promise.resolve(null); }, () => false);
  await settle();
  page.origin.value = 'https://keep.example';
  await page.clear();
  assert.equal(page.origin.value, 'https://keep.example');
  assert.deepEqual(calls, ['load_connection']);
  const ordinaryBrowser = launcher();
  await ordinaryBrowser.submitForm();
  await ordinaryBrowser.clear();
  assert.equal(ordinaryBrowser.submit.disabled, true);
  assert.match(ordinaryBrowser.status.textContent, /原生客户端/);
});
