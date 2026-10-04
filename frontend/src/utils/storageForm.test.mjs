import test from 'node:test';
import assert from 'node:assert/strict';
import { storageDraft, storagePayload, changeStorageKind } from './storageForm.ts';
const saved = (config = {}) => ({
  id: 'test',
  name: '云端',
  kind: 's3',
  config: { bucket: 'test-bucket', ...config },
  has_credentials: true,
  enabled: true,
});

test('unchanged storage keeps exact positioning config and omits existing secrets', () => {
  const original = saved({ endpoint: 'https://example.com', prefix: '', public_endpoint: null });
  const draft = storageDraft({ ...original, credentials: { secret_access_key: 'must-not-copy' } });
  const payload = storagePayload(draft);
  assert.deepEqual(payload.config, original.config);
  assert.deepEqual(draft.credentials, {});
  assert.equal(Object.hasOwn(payload, 'credentials'), false);
  assert.equal(Object.hasOwn(payload.config, 'region'), false);
});
test('new cloud profiles and partial credential edits require a complete pair', () => {
  const draft = storageDraft(saved());
  draft.credentials.session_token = 'new-test-token';
  assert.throws(() => storagePayload(draft), /完整填写/);
  draft.credentials.access_key_id = 'new-test-id';
  draft.credentials.secret_access_key = 'new-test-secret';
  assert.deepEqual(storagePayload(draft).credentials, draft.credentials);
  const empty = storageDraft();
  changeStorageKind(empty, 's3');
  empty.name = 'new';
  empty.config.bucket = 'bucket';
  assert.throws(() => storagePayload(empty), /完整填写/);
});
test('provider switches cannot retain or translate old cloud credentials', () => {
  const draft = storageDraft(saved());
  draft.credentials = { access_key_id: 'old-id', secret_access_key: 'old-secret' };
  changeStorageKind(draft, 'oss');
  assert.deepEqual(draft.credentials, {});
  assert.deepEqual(draft.config, {});
  draft.config = {
    bucket: 'oss-bucket',
    endpoint: 'https://oss-cn-hangzhou.aliyuncs.com',
    region: 'cn-hangzhou',
  };
  assert.throws(() => storagePayload(draft), /完整填写/);
  draft.credentials = { access_key_id: 'new-id', access_key_secret: 'new-secret' };
  assert.equal(storagePayload(draft).credentials.secret_access_key, undefined);
  changeStorageKind(draft, 'local');
  assert.deepEqual(storagePayload(draft).credentials, {});
  changeStorageKind(draft, 's3');
  draft.config.bucket = 'bucket';
  assert.throws(() => storagePayload(draft), /完整填写/);
});
test('endpoint origins reject credentials, paths and query strings', () => {
  for (const endpoint of [
    'file:///tmp',
    'https://user:pass@example.com',
    'https://example.com/path',
    'https://example.com?token=abc',
    'https://example.com/#x',
  ]) {
    const draft = storageDraft(saved({ endpoint }));
    assert.throws(() => storagePayload(draft), /HTTP/);
  }
  assert.equal(
    storagePayload(storageDraft(saved({ endpoint: 'http://minio:9000' }))).config.endpoint,
    'http://minio:9000',
  );
});
test('tuning bounds and disabled default fail before submission', () => {
  const draft = storageDraft(saved({ read_priority: -1 }));
  assert.throws(() => storagePayload(draft), /优先级/);
  draft.config.read_priority = 0;
  draft.config.part_size = 1024;
  assert.throws(() => storagePayload(draft), /分片大小/);
  draft.config.part_size = 16 * 1024 ** 2;
  draft.enabled = false;
  draft.is_default = true;
  assert.throws(() => storagePayload(draft), /必须启用/);
});
