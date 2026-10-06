import test from 'node:test';
import assert from 'node:assert/strict';
import { commentPart, commentTimestamps, collapsedComment } from './commentTimeline.ts';
const parts = [
  { id: 'p1', position: 1, duration: 300, playable: true },
  { id: 'p2', position: 2, duration: 4000, playable: true },
];
const context = { currentPartId: 'p1', parts };
const times = (text, part = parts[1]) =>
  commentTimestamps(text, part).filter((piece) => piece.kind === 'timestamp');

test('timestamps preserve fullwidth text, hours, zero and current-part boundaries', () => {
  const text = '0：00 开始\n4：02 中间\n1:02:03 结尾';
  assert.deepEqual(
    times(text).map((piece) => piece.seconds),
    [0, 242, 3723],
  );
  assert.equal(
    commentTimestamps(text, parts[1])
      .map((piece) => piece.text)
      .join(''),
    text,
  );
  assert.deepEqual(
    times('4:59 5:00 5:01', parts[0]).map((piece) => piece.seconds),
    [299],
  );
  assert.equal(times('0:00', { ...parts[0], duration: NaN }).length, 0);
  assert.equal(times('0:00', { ...parts[0], duration: 0 }).length, 0);
});
test('malformed times and source URLs never turn a valid suffix into a jump', () => {
  assert.equal(times('1:99 1:60:03 1:2:3:4 123456:00 a4:02 4:02b https://x/4:02/foo').length, 0);
  assert.deepEqual(
    times('跳4：02！ (1:02:03)').map((piece) => piece.seconds),
    [242, 3723],
  );
});
test('explicit part headings choose that playable part and do not fall back to another', () => {
  assert.equal(commentPart('part2@天阙 总结\n0：00 开始', context)?.id, 'p2');
  assert.equal(commentPart(' P2 0:00', context)?.id, 'p2');
  assert.equal(commentPart('4：02 开始', context)?.id, 'p1');
  assert.equal(commentPart('part3 0:00', context), undefined);
  assert.equal(
    commentPart('part2 0:00', { ...context, parts: [parts[0], { ...parts[1], playable: false }] }),
    undefined,
  );
});
test('long comments collapse by characters or lines without losing their full source', () => {
  assert.deepEqual(collapsedComment('短评论'), { text: '短评论', collapsed: false });
  assert.equal(collapsedComment('😀'.repeat(400)).text, '😀'.repeat(360) + '…');
  assert.equal(collapsedComment('a\nb\nc\nd\ne\nf\ng').text, 'a\nb\nc\nd\ne\nf…');
  assert.equal(collapsedComment('字'.repeat(359) + '4:02结束').text, '字'.repeat(359) + '4…');
});
