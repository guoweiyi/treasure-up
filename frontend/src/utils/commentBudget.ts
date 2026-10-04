export const commentBudgetDefaults = {
  comment_top_limit: 500,
  comment_scan_limit: 2000,
  comment_reply_total_limit: 200,
  comment_reply_per_root_limit: 10,
  comment_asset_count_limit: 300,
  comment_asset_bytes_limit: 25 * 1024 * 1024,
  asset_interval_seconds: 0.1,
};
export const commentBudgetFields = [
  { key: 'comment_top_limit', label: '热门评论保留上限', min: 0, max: 10000, step: 1 },
  { key: 'comment_scan_limit', label: '主评论扫描上限', min: 1, max: 100000, step: 1 },
  { key: 'comment_reply_total_limit', label: '回复总上限', min: 0, max: 100000, step: 1 },
  {
    key: 'comment_reply_per_root_limit',
    label: '每条主评论的回复上限',
    min: 0,
    max: 1000,
    step: 1,
  },
  { key: 'comment_asset_count_limit', label: '评论素材数量上限', min: 0, max: 10000, step: 1 },
  {
    key: 'comment_asset_bytes_limit',
    label: '评论素材容量（MiB）',
    min: 0,
    max: 1024,
    step: 1,
    factor: 1024 * 1024,
  },
  { key: 'asset_interval_seconds', label: '图片请求间隔（秒）', min: 0.05, max: 5, step: 0.05 },
] as const;
export function commentBudgetValue(value: string, factor = 1): number | undefined {
  if (value.trim() === '') return undefined;
  const number = Number(value);
  return Number.isFinite(number) ? number * factor : undefined;
}
