type Row = Record<string, any>;
export const jobNames: Record<string, string> = {
  scan_collection: '检查备份来源',
  archive_video: '归档视频资料',
  download_media: '下载视频原档',
  create_playback: '生成兼容副本',
  refresh_comments: '补采评论',
  verify_account: '验证账号',
  migrate_storage: '迁移存储',
  backup: '创建备份',
  probe_storage: '探测存储能力',
  refresh_stats: '更新视频统计',
  sync_storage: '同步存储节点',
  measure_storage: '测量存储性能',
  sync_video: '同步视频副本',
  prepare_media: '准备播放文件',
  retire_storage_location: '停用媒体副本',
  restore_storage_location: '恢复媒体副本',
  purge_storage_location: '永久删除副本',
};
const phases: Record<string, string> = {
  metadata: '读取视频资料',
  profiles: '读取 UP 主资料',
  cover_and_avatars: '保存封面与头像',
  basics_ready: '基础资料已就绪',
  danmaku: '保存弹幕',
  subtitles: '保存字幕',
  comments: '保存评论',
  images: '保存关联图片',
  metadata_ready: '资料归档完成',
  download: '下载原始媒体',
  media_ready: '原档已保存',
  compatible_copy: '生成兼容副本',
  compatible_ready: '兼容副本已就绪',
  pages: '检查来源分页',
  verify: '核对来源列表',
};
export function jobPhase(job: Row) {
  const phase =
    job.checkpoint?.progress?.phase ||
    job.result?.progress?.phase ||
    job.checkpoint?.source_scan?.phase;
  return (
    phases[phase] ||
    (job.status === 'queued'
      ? '等待工作进程'
      : job.status === 'succeeded'
        ? '执行完成'
        : '等待下一次进度更新')
  );
}
export function jobCounts(job: Row) {
  const cp = job.checkpoint || {},
    result = job.result || {},
    progress = cp.progress || {};
  const counts = cp.source_scan?.counts || result.counts || {};
  const pairs: [string, unknown][] = [];
  const names: Record<string, string> = {
    observed: '已检查',
    new_items: '新增条目',
    queued: '已排队',
    reused: '已复用',
    skipped: '已跳过',
    not_observed: '本轮不可见',
  };
  for (const [key, label] of Object.entries(names))
    if (typeof counts[key] === 'number') pairs.push([label, counts[key]]);
  const completed =
    progress.completed_parts ??
    result.completed ??
    (Array.isArray(cp.completed) ? cp.completed.length : undefined);
  const total = progress.total_parts ?? result.total ?? cp.total;
  if (completed != null)
    pairs.push(['已完成', total != null ? `${completed} / ${total}` : completed]);
  if (typeof progress.pending === 'number') pairs.push(['待处理', progress.pending]);
  return pairs;
}
export function finishedJob(job: Row) {
  return ['succeeded', 'failed', 'cancelled', 'paused', 'blocked', 'partial'].includes(job.status);
}
