import test from 'node:test';
import assert from 'node:assert/strict';
import { commentContent, localCommentAsset } from './commentContent.ts';
const emote = (text, id = 'saved') => ({ text, asset_url: `/api/v1/assets/${id}` });
test('emote tokens are literal, inline and repeated in the original content order', () => {
  const content = '你好[doge]\n[doge] <b>纯文字</b> [.*]';
  const pieces = commentContent(content, [emote('[doge]'), emote('[.*]', 'special')]);
  assert.equal(pieces.map((piece) => piece.text).join(''), content);
  assert.deepEqual(
    pieces.filter((piece) => piece.kind === 'emote').map((piece) => piece.text),
    ['[doge]', '[doge]', '[.*]'],
  );
  assert.ok(pieces.some((piece) => piece.kind === 'text' && piece.text.includes('<b>纯文字</b>')));
});
test('missing, failed or external emotes keep the original readable token', () => {
  const content = '[missing][remote][doge]';
  const pieces = commentContent(content, [
    { text: '[remote]', asset_url: 'https://external.test/image' },
  ]);
  assert.deepEqual(pieces, [{ kind: 'text', text: content }]);
  for (const url of [
    'javascript:alert(1)',
    '//external.test/x',
    '/api/v1/assets/x?token=a',
    '/api/v1/assets/../admin',
  ])
    assert.equal(localCommentAsset(url), undefined);
});
