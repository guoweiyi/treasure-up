import { commentContent, type CommentEmote, type ContentPiece } from './commentContent.ts';

export type CommentPart = { id: string; position: number; duration: number; playable: boolean };
export type CommentPlayback = { currentPartId: string; parts: CommentPart[] };
export type CommentSeek = { partId: string; seconds: number };

export function commentSections(content: string, playback?: CommentPlayback) {
  const sections: { text: string; part?: CommentPart }[] = [];
  let position = 0;
  let part = playback?.parts.find((item) => item.playable && item.id === playback.currentPartId);
  // Only a heading at a line start changes the target for the following coordinates.
  const headings = /^[^\S\r\n]*(?:part|p)[^\S\r\n]*([1-9]\d{0,3})(?!\d)/gim;
  for (const heading of content.matchAll(headings)) {
    if (heading.index > position)
      sections.push({ text: content.slice(position, heading.index), part });
    part = playback?.parts.find((item) => item.playable && item.position === Number(heading[1]));
    position = heading.index;
  }
  sections.push({ text: content.slice(position), part });
  return sections;
}

export function commentDisplay(
  content: string,
  emotes: CommentEmote[] = [],
  playback?: CommentPlayback,
) {
  // Keep each section's part across inline emotes; splitting text first loses this context.
  return commentSections(content, playback).flatMap(({ text, part }) =>
    commentContent(text, emotes).flatMap<
      ContentPiece | ReturnType<typeof commentTimestamps>[number]
    >((piece) => (piece.kind === 'text' ? commentTimestamps(piece.text, part) : [piece])),
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
