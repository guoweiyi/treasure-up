export type EditableVideo = {
  source_title?: string;
  source_description?: string;
  title?: string;
  description?: string;
  cover_url?: string;
  bvid?: string;
  duration?: number;
  notes?: string;
  tags?: string[];
  source_tags?: string[];
  manual_tags?: string[];
};
export function videoEditorDraft(video: EditableVideo) {
  return {
    source_title: video.source_title ?? video.title ?? '',
    source_description: video.source_description ?? '',
    title: video.title ?? video.source_title ?? '',
    description: video.description ?? video.source_description ?? '',
    cover_url: video.cover_url ?? '',
    bvid: video.bvid ?? '',
    duration: video.duration ?? 0,
    notes: video.notes ?? '',
    tags: [...(video.manual_tags ?? video.tags ?? [])],
    source_tags: [...(video.source_tags ?? [])],
  };
}
export function videoEditorPayload(value: ReturnType<typeof videoEditorDraft>) {
  const title = value.title.trim();
  if (!title) throw new Error('请填写视频标题，或恢复来源标题。');
  return {
    title_override: title === value.source_title ? null : title,
    description_override: value.description === value.source_description ? null : value.description,
    notes: value.notes,
    tags: videoTags(value.tags),
  };
}
export function videoTags(current: string[], addition = '') {
  const tags = [
    ...new Set(
      [...current, addition]
        .flatMap((tag) => tag.split(/[,，\n]/))
        .map((tag) => tag.trim())
        .filter(Boolean),
    ),
  ];
  if (tags.length > 50) throw new Error('最多保存 50 个标签。');
  if (tags.some((tag) => tag.length > 100)) throw new Error('每个标签最多 100 字。');
  return tags;
}
