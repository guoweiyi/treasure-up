import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deliveryHost, networkSample } from './runtimeStats.ts';
test('statistics never reveal signed paths or tokens and do not invent transfer measurements', () => {
  assert.equal(
    deliveryHost('https://cdn.example:443/secret/path?token=secret#hash'),
    'cdn.example',
  );
  assert.equal(
    deliveryHost('/api/media?signature=secret', 'https://library.example'),
    'library.example',
  );
  assert.equal(deliveryHost('blob:https://library.example/secret'), null);
  const measured = {
    transferSize: 1024,
    encodedBodySize: 1000,
    responseStart: 10,
    responseEnd: 20,
  };
  assert.equal(networkSample(measured), 800000);
  assert.equal(networkSample({ ...measured, transferSize: 0 }), null);
  assert.equal(networkSample({ ...measured, responseStart: 0 }), null);
  assert.equal(networkSample({ ...measured, responseEnd: 10 }), null);
  assert.equal(networkSample({ ...measured, responseStatus: 304 }), null);
  assert.equal(networkSample({ ...measured, deliveryType: 'cache' }), null);
  assert.equal(
    networkSample({ ...measured, transferSize: 300, encodedBodySize: 10_000_000 }),
    null,
  );
  assert.equal(networkSample({ ...measured, transferSize: 300, encodedBodySize: 100 }), null);
});
