export function count(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value >= 100000000) return `${(value / 100000000).toFixed(1).replace(/\.0$/, '')}亿`;
  if (value >= 10000) return `${(value / 10000).toFixed(1).replace(/\.0$/, '')}万`;
  return String(value);
}

export function shortDate(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleDateString('zh-CN');
}

export function qualityLabel(variant: {
  quality?: number | string;
  width?: number;
  height?: number;
}) {
  const quality = String(variant.quality ?? '').trim();
  if (quality && !/^\d+$/.test(quality)) return quality;
  if (variant.width && variant.height) return `${Math.min(variant.width, variant.height)}P`;
  if (quality) return `${quality}P`;
  return variant.height ? `${variant.height}P` : '原始规格';
}
