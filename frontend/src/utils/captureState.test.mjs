import test from 'node:test';
import assert from 'node:assert/strict';
import {
  finishedJob,
  jobStatusLabel,
  mediaCaptureLabel,
  jobPhase,
  compatibilityNotice,
} from './jobDisplay.ts';

test('metadata success is labelled separately and child failure remains visible', () => {
  const parent = { kind: 'archive_video', status: 'succeeded', capture: { media: 'failed' } };
  assert.equal(jobStatusLabel(parent), '资料已完成');
  assert.equal(mediaCaptureLabel(parent), '视频下载失败');
  assert.equal(finishedJob(parent), true);
});
test('metadata result keeps polling until the media lane has stopped', () => {
  for (const media of ['running', 'queued']) {
    assert.equal(
      finishedJob({ kind: 'archive_video', status: 'succeeded', capture: { media } }),
      false,
    );
  }
  for (const media of ['complete', 'disabled', 'awaiting_consent', 'paused']) {
    assert.equal(
      finishedJob({ kind: 'archive_video', status: 'succeeded', capture: { media } }),
      true,
    );
    assert.notEqual(mediaCaptureLabel({ capture: { media } }), '');
  }
  assert.equal(finishedJob({ kind: 'probe_storage', status: 'succeeded' }), true);
});

test('unsupported HDR copy is a finished capability result, not a generated copy', () => {
  const job = {
    kind: 'create_playback',
    status: 'succeeded',
    result: {
      compatibility: 'unsupported',
      reason: 'hdr_conversion_unsupported',
      variant_id: null,
    },
    checkpoint: { progress: { phase: 'compatible_unsupported' } },
  };
  assert.equal(jobStatusLabel(job), '未生成兼容副本');
  assert.equal(jobPhase(job), '此格式暂不支持生成兼容副本');
  assert.equal(
    compatibilityNotice(job),
    'HDR/广色域到 SDR 的兼容副本暂不支持，原档已保留，未生成兼容副本。',
  );
  assert.equal(finishedJob(job), true);
  assert.equal(compatibilityNotice({ ...job, kind: 'download_media' }), '');
  assert.equal(compatibilityNotice({ ...job, status: 'failed' }), '');
  assert.equal(jobStatusLabel({ ...job, result: { variant_id: 'created-copy' } }), '');
  assert.equal(
    compatibilityNotice({ ...job, result: { compatibility: 'unsupported', reason: 'other' } }),
    '',
  );
});

test('source cooldown overrides stale running phase only with a known safe source error', () => {
  const now = Date.parse('2026-10-05T10:00:00Z');
  const job = {
    status: 'queued',
    error: '源站风控，请稍后恢复',
    available_at: '2026-10-05T10:05:00Z',
    checkpoint: { progress: { phase: 'comments' } },
  };
  assert.equal(jobPhase(job, now), '等待源站冷却后重试');
  assert.equal(jobPhase({ ...job, error: '账号仍在源站冷却期' }, now), '等待源站冷却后重试');
  assert.equal(jobPhase({ ...job, available_at: '2026-10-05T09:59:00Z' }, now), '等待工作进程重试');
  assert.equal(jobPhase({ ...job, available_at: 'invalid' }, now), '等待工作进程重试');
  assert.equal(jobPhase({ ...job, available_at: null }, now), '等待工作进程重试');
  assert.equal(jobPhase({ ...job, status: 'running' }, now), '保存评论');
  assert.equal(jobPhase({ ...job, status: 'failed' }, now), '保存评论');
  assert.equal(jobPhase({ ...job, error: '网络连接中断' }, now), '保存评论');
  assert.equal(jobPhase({ status: 'queued', available_at: job.available_at }, now), '等待工作进程');
});
