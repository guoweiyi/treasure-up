import type { Danmaku } from '../types';

export function normalizeDanmaku(rows: Danmaku[]) {
  return rows
    .filter((row) => Number.isFinite(row.time) && [0, 1, 2].includes(row.mode))
    .map((row) => ({
      text: String(row.text ?? ''),
      time: row.time,
      mode: row.mode as 0 | 1 | 2,
      color:
        typeof row.color === 'number' && Number.isFinite(row.color)
          ? `#${Math.max(0, Math.min(0xffffff, Math.round(row.color)))
              .toString(16)
              .padStart(6, '0')}`
          : typeof row.color === 'string' && /^#(?:[\da-f]{3}|[\da-f]{6})$/i.test(row.color)
            ? row.color
            : '#ffffff',
    }));
}

// This ancillary task is intentionally not awaited by player setup. Its result
// belongs to a specific player, so late data/errors must not affect a new part.
export async function loadDanmaku(
  load: () => Promise<Danmaku[]>,
  current: () => boolean,
  apply: (rows: ReturnType<typeof normalizeDanmaku>) => void | Promise<void>,
  failed: (error: unknown) => void,
) {
  try {
    const rows = await load();
    if (!current()) return;
    await apply(normalizeDanmaku(rows));
  } catch (error) {
    if (current()) failed(error);
  }
}
