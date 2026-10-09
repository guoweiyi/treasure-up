type Row = Record<string, any>;
export function compatibilityNotice(job: Row) {
  if (job.kind !== 'create_playback' || job.status !== 'succeeded') return '';
  if (job.result?.compatibility === 'not_needed')
    return '原档无需自动生成兼容副本，已跳过额外转码；能否播放仍取决于设备对原编码的支持。';
  return job.result?.compatibility === 'unsupported' &&
    job.result?.reason === 'hdr_conversion_unsupported'
    ? 'HDR/广色域到 SDR 的兼容副本暂不支持，原档已保留，未生成兼容副本。'
    : '';
}
export function jobStatusLabel(job: Row) {
  if (compatibilityNotice(job))
    return job.result?.compatibility === 'not_needed' ? '无需兼容副本' : '未生成兼容副本';
  return job.kind === 'archive_video' && job.status === 'succeeded' ? '资料已完成' : '';
}
export function mediaCaptureLabel(job: Row) {
  const labels: Record<string, string> = {
    complete: '视频原档已保存',
    queued: '视频下载等待执行',
    running: '视频原档下载中',
    failed: '视频下载失败',
    partial: '视频下载未完成',
    blocked: '视频下载受阻',
    paused: '视频下载已暂停',
    cancelled: '视频下载已取消',
    disabled: '本次仅采集资料',
    not_started: '视频下载尚未开始',
    awaiting_consent: '充电视频等待采集确认',
    missing: '视频原档不完整',
  };
  return labels[job.capture?.media] || '';
}
export const jobNames: Record<string, string> = {
  scan_collection: '检查备份来源',
  archive_video: '归档视频资料',
  delete_video: '删除视频归档',
  delete_creator: '删除 UP 主归档',
  download_media: '下载视频原档',
  create_playback: '生成兼容副本',
  refresh_comments: '补采评论',
  verify_account: '验证账号',
  refresh_credentials: '检查账号续期',
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
export function credentialRefreshNotice(job: Row) {
  if (job.kind !== 'refresh_credentials' || job.status !== 'succeeded') return '';
  const status = job.result?.refresh_status;
  if (status === 'ready')
    return job.result.rotated ? '账号凭据已续期。' : '账号凭据仍然有效，暂时无需续期。';
  const labels: Record<string, string> = {
    unsupported: '尚未配置刷新令牌，请重新扫码或补入配套的刷新令牌。',
    paused: '自动续期已关闭。',
    pending_confirm: '新凭据已保存，正在等待源站确认。',
    relogin_required: '需要重新授权，请在 B 站账号页面扫码登录。',
    error: '续期检查未完成，请在 B 站账号页面查看状态。',
  };
  return labels[status] || '';
}
const phases: Record<string, string> = {
  deletion_wait: '等待关联任务停止',
  deletion_records: '清理归档资料',
  deletion_assets: '清理不再使用的文件',
  deletion_complete: '归档清理完成',
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
  compatible_not_needed: '无需兼容副本',
  compatible_unsupported: '此格式暂不支持生成兼容副本',
  pages: '检查来源分页',
  verify: '核对来源列表',
};
const sourceCooldownErrors = new Set([
  '源站限流或风控，请稍后恢复任务',
  '源站风控，请稍后恢复',
  '源站认证检查未通过',
  '源站要求验证，请暂停并检查账号',
  '弹幕源站风控，请稍后恢复',
  '账号仍在源站冷却期',
  '账号仍在源站冷却期，任务将延后',
]);
export function jobPhase(job: Row, now = Date.now()) {
  // These are server-sanitized domain errors. Do not infer a source cooldown
  // from arbitrary failures or the last running checkpoint alone.
  if (job.status === 'queued' && sourceCooldownErrors.has(job.error)) {
    const availableAt = typeof job.available_at === 'string' ? Date.parse(job.available_at) : NaN;
    return Number.isFinite(availableAt) && availableAt > now
      ? '等待源站冷却后重试'
      : '等待工作进程重试';
  }
  if (job.status === 'queued') {
    if (job.error === '已有视频正在下载，等待串行下载时段') return '等待串行下载';
    if (
      [
        '等待下一次串行视频下载时段',
        '等待下一次媒体下载时段',
        '等待同账号下一次媒体下载时段',
      ].includes(job.error)
    )
      return '等待下载间隔';
    if (job.error === '账号请求正在排队，请稍后重试') return '等待账号请求间隔';
  }
  const phase =
    job.checkpoint?.progress?.phase ||
    job.result?.progress?.phase ||
    job.checkpoint?.source_scan?.phase ||
    job.checkpoint?.phase;
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
  if (job.kind === 'delete_video' || job.kind === 'delete_creator') {
    if (typeof result.deleted_videos === 'number')
      pairs.push([
        '已清理视频',
        `${result.deleted_videos} / ${result.total_videos ?? result.deleted_videos}`,
      ]);
    if (typeof result.processed_assets === 'number')
      pairs.push([
        '已核对文件',
        `${result.processed_assets} / ${result.total_assets ?? result.processed_assets}`,
      ]);
    if (typeof result.deleted_assets === 'number')
      pairs.push(['已删除文件', result.deleted_assets]);
    if (result.retained_shared_assets) pairs.push(['保留共用文件', result.retained_shared_assets]);
    if (result.retained_backup_assets) pairs.push(['保留备份文件', result.retained_backup_assets]);
    if (result.collaborations_detached)
      pairs.push(['移除联合署名', result.collaborations_detached]);
  }
  return pairs;
}
export function finishedJob(job: Row) {
  if (job.kind === 'archive_video' && ['queued', 'running'].includes(job.capture?.media))
    return false;
  return ['succeeded', 'failed', 'cancelled', 'paused', 'blocked', 'partial'].includes(job.status);
}
