import test from 'node:test';
import assert from 'node:assert/strict';
import { videoEditorDraft, videoEditorPayload, videoTags } from './videoEditor.ts';
import { commentBudgetValue } from './commentBudget.ts';
test('automatic source tags remain separate when an editor saves unrelated metadata', () => {
  const draft = videoEditorDraft({
    title: '视频',
    tags: ['来源标签', '手动'],
    source_tags: ['来源标签'],
    manual_tags: ['手动'],
  });
  assert.deepEqual(draft.source_tags, ['来源标签']);
  assert.deepEqual(videoEditorPayload(draft).tags, ['手动']);
  const inherited = videoEditorDraft({
    title: '视频',
    tags: ['来源标签'],
    source_tags: ['来源标签'],
    manual_tags: [],
  });
  assert.deepEqual(videoEditorPayload(inherited).tags, []);
});
test('unchanged visible source fields remain inherited rather than becoming overrides', () => {
  const draft = videoEditorDraft({
    source_title: '来源标题',
    title: '来源标题',
    source_description: '来源简介',
    description: '来源简介',
    tags: ['教程'],
  });
  assert.deepEqual(videoEditorPayload(draft), {
    title_override: null,
    description_override: null,
    notes: '',
    tags: ['教程'],
  });
});
test('editing and restoring fields preserve explicit blank description and independent tags', () => {
  const source = {
    source_title: '来源',
    title: '本地标题',
    source_description: '原简介',
    description: '',
    tags: ['旧标签'],
    notes: '整理',
  };
  const draft = videoEditorDraft(source);
  draft.tags.push('新标签，旧标签');
  assert.deepEqual(source.tags, ['旧标签']);
  assert.deepEqual(videoEditorPayload(draft), {
    title_override: '本地标题',
    description_override: '',
    notes: '整理',
    tags: ['旧标签', '新标签'],
  });
  draft.title = draft.source_title;
  draft.description = draft.source_description;
  assert.equal(videoEditorPayload(draft).title_override, null);
  assert.equal(videoEditorPayload(draft).description_override, null);
});
test('empty titles and too many labels are rejected before submitting', () => {
  const draft = videoEditorDraft({ title: '标题' });
  draft.title = ' ';
  assert.throws(() => videoEditorPayload(draft), /标题/);
  draft.title = '标题';
  draft.tags = Array.from({ length: 51 }, (_, i) => `${i}`);
  assert.throws(() => videoEditorPayload(draft), /50/);
});
test('comment budget inputs distinguish inherit from zero and convert MiB exactly', () => {
  assert.equal(commentBudgetValue(''), undefined);
  assert.equal(commentBudgetValue('0'), 0);
  assert.equal(commentBudgetValue('25', 1024 * 1024), 26214400);
  assert.equal(commentBudgetValue('0.1'), 0.1);
});
test('explicit tag add trims and deduplicates without mutating current tags', () => {
  const tags = ['教程'];
  assert.deepEqual(videoTags(tags, ' 教程，旅行,学习 '), ['教程', '旅行', '学习']);
  assert.deepEqual(tags, ['教程']);
  assert.deepEqual(videoTags(tags, ''), ['教程']);
  assert.throws(() => videoTags(tags, '长'.repeat(101)), /100/);
  const full = Array.from({ length: 50 }, (_, i) => `tag${i}`);
  assert.equal(videoTags(full, 'tag0').length, 50);
  assert.throws(() => videoTags(full, 'extra'), /50/);
});
