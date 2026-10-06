/** Plugin lanes are shared by all modes. Keep their logical coordinates intact,
 * then translate fixed-bottom lanes only; offsetTop/collision detection is unchanged. */
export function danmakuLayout(
  width: number,
  height: number,
  videoWidth: number,
  videoHeight: number,
  area: number,
  subtitleSafe: boolean,
  safeInset = 0,
  fit: 'contain' | 'cover' = 'contain',
) {
  const h = Number.isFinite(height) && height > 0 ? height : 450;
  const ratio = videoWidth > 0 && videoHeight > 0 ? videoWidth / videoHeight : 0;
  const pictureHeight =
    fit === 'contain' && ratio > 0 && width > 0 ? Math.min(h, width / ratio) : h;
  const letterbox = (h - pictureHeight) / 2;
  const top = letterbox + Math.min(12, pictureHeight * 0.05);
  const controls = Math.max(0, 52 + Math.max(0, safeInset) - letterbox);
  const reserve = Math.max(controls, subtitleSafe ? pictureHeight * 0.18 : 0, 12);
  const maxBottom = Math.max(0, h - top - Math.min(36, pictureHeight));
  const fixedBottom = Math.min(maxBottom, letterbox + reserve);
  const fraction = Number.isFinite(area) ? Math.max(25, Math.min(100, area)) / 100 : 0.5;
  const bottom = Math.min(
    maxBottom,
    Math.max(fixedBottom, letterbox + pictureHeight * (1 - fraction)),
  );
  return { margin: [top, bottom] as [number, number], bottomOffset: bottom - fixedBottom };
}

/** Compatibility helper for callers without source dimensions. */
export function danmakuMargins(
  height: number,
  area: number,
  subtitleSafe: boolean,
  safeInset = 0,
): [number, number] {
  return danmakuLayout(0, height, 0, 0, area, subtitleSafe, safeInset).margin;
}

export const playbackRates = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 3];
export const rateChoices = playbackRates.map((value) => ({
  value,
  label: `${value}×${value === 1 ? ' 正常' : ''}`,
}));
