import test from 'node:test';
import assert from 'node:assert/strict';
import {
  commentSections,
  commentTimestamps,
  commentDisplay,
  collapsedComment,
} from './commentTimeline.ts';
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
  assert.equal(commentSections('part2@天阙 总结\n0：00 开始', context)[0].part?.id, 'p2');
  assert.equal(commentSections(' P2 0:00', context)[0].part?.id, 'p2');
  assert.equal(commentSections('4：02 开始', context)[0].part?.id, 'p1');
  assert.equal(commentSections('part3 0:00', context)[0].part, undefined);
  assert.equal(
    commentSections('part2 0:00', {
      ...context,
      parts: [parts[0], { ...parts[1], playable: false }],
    })[0].part,
    undefined,
  );
});
test('long comments collapse by characters or lines without losing their full source', () => {
  assert.deepEqual(collapsedComment('短评论'), { text: '短评论', collapsed: false });
  assert.equal(collapsedComment('😀'.repeat(400)).text, '😀'.repeat(360) + '…');
  assert.equal(collapsedComment('a\nb\nc\nd\ne\nf\ng').text, 'a\nb\nc\nd\ne\nf…');
  assert.equal(collapsedComment('字'.repeat(359) + '4:02结束').text, '字'.repeat(359) + '4…');
});

test('mixed part directories use the closest preceding line heading across emotes', () => {
  const text =
    '总结一下其他佬的传送门~\n0:15 前言\npart1@某位\n00:40 开场[笑]\n4:59 结尾\npart2@天阙QAQ 总结~\n0：00不需要陪伴的闲暇[笑]4：02冲动是小天使\n40：47离去之原';
  const pieces = commentDisplay(
    text,
    [{ text: '[笑]', asset_url: '/api/v1/assets/emote' }],
    context,
  );
  assert.equal(pieces.map((piece) => piece.text).join(''), text);
  assert.equal(pieces.filter((piece) => piece.kind === 'emote').length, 2);
  assert.deepEqual(
    pieces
      .filter((piece) => piece.kind === 'timestamp')
      .map(({ partId, seconds }) => [partId, seconds]),
    [
      ['p1', 15],
      ['p1', 40],
      ['p1', 299],
      ['p2', 0],
      ['p2', 242],
      ['p2', 2447],
    ],
  );
  const changedPart = commentDisplay(text, [], { ...context, currentPartId: 'p2' }).filter(
    (piece) => piece.kind === 'timestamp',
  );
  assert.equal(changedPart[0].partId, 'p2');
  assert.equal(changedPart[1].partId, 'p1');
});

test('unknown or unarchived sections stay plain until a new valid heading and respect each duration', () => {
  const text =
    '0:01 默认\r\nPART1 5:00 超时\r\n p3@未归档\r\n0:10 保留\r\npart9 0:12 不存在\r\n仍有0:13\r\nP2 5:00 可播放\r\n文内提到part1 不切换 6:00';
  const pieces = commentDisplay(text, [], {
    ...context,
    parts: [...parts, { id: 'p3', position: 3, duration: 300, playable: false }],
  });
  assert.equal(pieces.map((piece) => piece.text).join(''), text);
  assert.deepEqual(
    pieces
      .filter((piece) => piece.kind === 'timestamp')
      .map(({ partId, seconds }) => [partId, seconds]),
    [
      ['p1', 1],
      ['p2', 300],
      ['p2', 360],
    ],
  );
});

test('collapsed and expanded directories retain the same target for visible coordinates', () => {
  const text =
    '目录\npart1 0:10 第一段[笑]\npart2@天阙\n0：00 开始[笑]\n4：02 中段\n7：16 第六行\n40：47 末尾';
  const emotes = [{ text: '[笑]', asset_url: '/api/v1/assets/emote' }];
  const preview = commentDisplay(collapsedComment(text).text, emotes, context).filter(
    (piece) => piece.kind === 'timestamp',
  );
  const full = commentDisplay(text, emotes, context).filter((piece) => piece.kind === 'timestamp');
  assert.deepEqual(
    preview.map(({ partId, seconds }) => [partId, seconds]),
    full.slice(0, preview.length).map(({ partId, seconds }) => [partId, seconds]),
  );
  assert.equal(full.at(-1).partId, 'p2');
});
