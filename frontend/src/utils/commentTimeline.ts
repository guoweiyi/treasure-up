export type CommentPart = { id: string; position: number; duration: number; playable: boolean };
export type CommentPlayback = { currentPartId: string; parts: CommentPart[] };
export type CommentSeek = { partId: string; seconds: number };

export function commentPart(content: string, playback?: CommentPlayback): CommentPart | undefined {
  if (!playback) return undefined;
  const explicit = /^\s*(?:part|p)\s*([1-9]\d{0,3})(?!\d)/i.exec(content);
  return playback.parts.find(
    (part) =>
      part.playable &&
      (explicit ? part.position === Number(explicit[1]) : part.id === playback.currentPartId),
  );
}

export function commentTimestamps(content: string, part?: CommentPart) {
  const result: (
    | { kind: 'text'; text: string }
    | { kind: 'timestamp'; text: string; seconds: number; partId: string; position: number }
  )[] = [];
  if (!part || !Number.isFinite(part.duration) || part.duration <= 0)
    return [{ kind: 'text' as const, text: content }];
  // Consume the complete colon sequence so malformed times cannot become valid suffix links.
  const pattern = /(?<![\w:：/])\d{1,5}(?:[:：]\d{1,2}){1,3}(?![\w:：/])/g;
  let position = 0;
  for (const match of content.matchAll(pattern)) {
    const fields = match[0].split(/[:：]/).map(Number);
    if (fields.length > 3 || fields.at(-1)! >= 60 || (fields.length === 3 && fields[1]! >= 60))
      continue;
    const seconds = fields.reduce((total, value) => total * 60 + value, 0);
    if (seconds >= part.duration) continue;
    if (match.index > position)
      result.push({ kind: 'text', text: content.slice(position, match.index) });
    result.push({
      kind: 'timestamp',
      text: match[0],
      seconds,
      partId: part.id,
      position: part.position,
    });
    position = match.index + match[0].length;
  }
  if (position < content.length || !result.length)
    result.push({ kind: 'text', text: content.slice(position) });
  return result;
}

export function collapsedComment(content: string, limit = 360, lines = 6) {
  const points = Array.from(content);
  let end = Math.min(points.length, limit),
    breaks = 0;
  for (let index = 0; index < end; index++) {
    if (points[index] === '\n' && ++breaks >= lines) {
      end = index;
      break;
    }
  }
  if (end >= points.length) return { text: content, collapsed: false };
  // Avoid cutting in the middle of a timestamp or an emote token where possible.
  const prefix = points
    .slice(0, end)
    .join('')
    .replace(/(?:\[[^\]\n]{0,100}|\d+[:：][\d:：]*)$/, '');
  return { text: prefix.trimEnd() + '…', collapsed: true };
}
