export interface ContentFeatures {
  charging_exclusive?: boolean;
  dolby_vision?: boolean;
  dolby_atmos?: boolean;
}
export function contentBadges(features?: ContentFeatures) {
  return [
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
