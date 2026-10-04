/** Keep fixed bottom comments above transport controls and the subtitle area. */
export function danmakuMargins(
  height: number,
  area: number,
  subtitleSafe: boolean,
  safeInset = 0,
): [number, number] {
  const boundedHeight = Number.isFinite(height) && height > 0 ? height : 450;
  const top = Math.min(12, boundedHeight * 0.05);
  const reserved = Math.max(52 + Math.max(0, safeInset), subtitleSafe ? boundedHeight * 0.18 : 0);
  // Very short landscape players must retain at least one usable text row.
  const bottom = Math.min(
    Math.max(0, boundedHeight - top - 36),
    Math.max(reserved, boundedHeight * (1 - Math.max(25, Math.min(100, area)) / 100)),
  );
  return [top, Math.round(bottom)];
}

export const playbackRates = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 3];
export const rateChoices = playbackRates.map((value) => ({
  value,
  label: `${value}×${value === 1 ? ' 正常' : ''}`,
}));
