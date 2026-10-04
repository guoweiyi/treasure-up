import type { QueueMode } from './queue';

export const queueModes: { value: QueueMode; label: string }[] = [
  { value: 'continuous', label: '列表连播' },
  { value: 'repeat', label: '单集循环' },
  { value: 'pause', label: '播完暂停' },
];
export function queuePosition(index: number, total: number, partPosition?: number, partCount = 0) {
  const video = total > 0 ? `${index >= 0 ? index + 1 : '—'} / ${total}` : '';
  const part =
    partCount > 1 ? `P${partPosition && partPosition > 0 ? partPosition : '—'} / ${partCount}` : '';
  return { video, part };
}
export function queueMenuFocus(key: string, current: number, count: number): number | null {
  if (!count) return null;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  if (key === 'ArrowDown') return (current + 1 + count) % count;
  if (key === 'ArrowUp') return current < 0 ? count - 1 : (current - 1 + count) % count;
  return null;
}
