export interface ContentFeatures {
  source_unavailable?: boolean;
  charging_exclusive?: boolean;
  dolby_vision?: boolean;
  dolby_atmos?: boolean;
}
export function contentBadges(features?: ContentFeatures) {
  return [
    {
      kind: 'unavailable' as const,
      show: features?.source_unavailable === true,
      text: '源站已失效',
      title: '源站稿件信息接口已明确返回不存在，本地归档保留；不表示已确认删除原因',
    },
    {
      kind: 'charging' as const,
      show: features?.charging_exclusive === true,
      text: '充电专属',
      title: '源站标记为充电专属内容',
    },
    {
      kind: 'vision' as const,
      show: features?.dolby_vision === true,
      text: 'Dolby Vision',
      title: '归档包含杜比视界；实际呈现取决于所选版本、浏览器和显示设备',
    },
    {
      kind: 'atmos' as const,
      show: features?.dolby_atmos === true,
      text: 'Dolby Atmos',
      title: '归档包含杜比全景声音轨；不代表当前设备正在输出 Atmos',
    },
  ].filter((badge) => badge.show);
}
