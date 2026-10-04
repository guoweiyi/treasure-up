export type CommentEmote = { text: string; asset_url: string };
export type ContentPiece =
  | { kind: 'text'; text: string }
  | { kind: 'emote'; text: string; url: string };
export function localCommentAsset(url?: string): string | undefined {
  return url && /^\/api\/v1\/assets\/[A-Za-z0-9_-]+$/.test(url) ? url : undefined;
}
export function commentContent(content: string, emotes: CommentEmote[] = []): ContentPiece[] {
  const images = new Map(
    emotes
      .filter((emote) => emote.text && localCommentAsset(emote.asset_url))
      .map((emote) => [emote.text, emote.asset_url]),
  );
  if (!images.size) return [{ kind: 'text', text: content }];
  const alternatives = [...images.keys()]
    .sort((a, b) => b.length - a.length)
    .map((text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const pattern = new RegExp(alternatives.join('|'), 'g');
  const pieces: ContentPiece[] = [];
  let position = 0;
  for (const match of content.matchAll(pattern)) {
    const at = match.index!;
    if (at > position) pieces.push({ kind: 'text', text: content.slice(position, at) });
    pieces.push({ kind: 'emote', text: match[0], url: images.get(match[0])! });
    position = at + match[0].length;
  }
  if (position < content.length) pieces.push({ kind: 'text', text: content.slice(position) });
  return pieces;
}
