export interface DanmakuPreferences {
  visible: boolean;
  opacity: number;
  fontSize: number;
  area: number;
  speed: number;
  density: number;
  offset: number;
  fontFamily: string;
  customFont: string;
  weight: number;
  spacing: number;
  outline: string;
  strokeWidth: number;
  strokeColor: string;
  uniform: boolean;
  color: string;
  rolling: boolean;
  top: boolean;
  bottom: boolean;
  colored: boolean;
  antiOverlap: boolean;
  synchronousPlayback: boolean;
  scaleWithScreen: boolean;
  subtitleSafe: boolean;
}

export const preferenceKey = 'treasure-up:danmaku:v1';
export const fontOptions = [
  ['Microsoft YaHei', '微软雅黑'],
  ['SimHei', '黑体'],
  ['SimSun', '宋体'],
  ['KaiTi', '楷体'],
  ['sans-serif', '系统无衬线'],
  ['monospace', '等宽字体'],
  ['custom', '自定义本机字体'],
] as const;

export function defaultPreferences(visible = true): DanmakuPreferences {
  return {
    visible,
    opacity: 100,
    fontSize: 25,
    area: 75,
    speed: 5,
    density: 100,
    offset: 0,
    fontFamily: 'Microsoft YaHei',
    customFont: '',
    weight: 400,
    spacing: 0,
    outline: 'stroke',
    strokeWidth: 1,
    strokeColor: '#000000',
    uniform: false,
    color: '#ffffff',
    rolling: true,
    top: true,
    bottom: true,
    colored: true,
    antiOverlap: true,
    synchronousPlayback: false,
    scaleWithScreen: false,
    subtitleSafe: true,
  };
}

export function clamp(value: number, min: number, max: number) {
  return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : min;
}

/** Saved preferences are untrusted browser data, including older versions. */
export function readPreferences(raw: string | null, visible = true): DanmakuPreferences {
  const result = defaultPreferences(visible);
  let saved: Record<string, unknown>;
  try {
    const value: unknown = JSON.parse(raw || '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return result;
    saved = value as Record<string, unknown>;
  } catch {
    return result;
  }
  for (const key of Object.keys(result) as (keyof DanmakuPreferences)[]) {
    if (typeof result[key] === 'boolean' && typeof saved[key] === 'boolean')
      (result as unknown as Record<string, unknown>)[key] = saved[key];
  }
  const bounds = {
    opacity: [0, 100],
    fontSize: [12, 120],
    area: [25, 100],
    speed: [1, 10],
    density: [10, 100],
    offset: [-60, 60],
    spacing: [0, 6],
    strokeWidth: [0, 3],
  } as const;
  for (const key of Object.keys(bounds) as (keyof typeof bounds)[]) {
    const value = saved[key];
    if (typeof value === 'number' && Number.isFinite(value))
      result[key] = clamp(value, bounds[key][0], bounds[key][1]);
  }
  if (fontOptions.some(([value]) => value === saved.fontFamily))
    result.fontFamily = String(saved.fontFamily);
  if (typeof saved.customFont === 'string') result.customFont = saved.customFont.slice(0, 100);
  if ([400, 500, 600, 700].includes(Number(saved.weight))) result.weight = Number(saved.weight);
  if (['stroke', 'heavy', 'shadow', 'none'].includes(String(saved.outline)))
    result.outline = String(saved.outline);
  for (const key of ['color', 'strokeColor'] as const)
    if (typeof saved[key] === 'string' && /^#[\da-f]{6}$/i.test(saved[key]))
      result[key] = saved[key];
  return result;
}

export function fontFamily(prefs: DanmakuPreferences) {
  return prefs.fontFamily === 'custom' ? prefs.customFont || 'sans-serif' : prefs.fontFamily;
}

export function textShadow(prefs: DanmakuPreferences) {
  const s = clamp(prefs.strokeWidth, 0, 3);
  const c = /^#[\da-f]{6}$/i.test(prefs.strokeColor) ? prefs.strokeColor : '#000000';
  if (prefs.outline === 'none') return 'none';
  if (prefs.outline === 'shadow') return `${s + 1}px ${s + 1}px 2px ${c}`;
  return `${s}px 0 ${prefs.outline === 'heavy' ? 2 : 0}px ${c}, -${s}px 0 0 ${c}, 0 ${s}px 0 ${c}, 0 -${s}px 0 ${c}`;
}

/** Keep the same sample on replay; density never changes archived data. */
export function includeAtDensity(index: number, density: number) {
  return (index * 37) % 100 < clamp(density, 10, 100);
}
