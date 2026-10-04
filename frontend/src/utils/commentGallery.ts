import { localCommentAsset } from './commentContent.ts';

export function commentGalleryImages(
  images: Array<string | { url?: string; asset_url?: string }> = [],
): string[] {
  return [
    ...new Set(
      images
        .map((image) =>
          localCommentAsset(typeof image === 'string' ? image : image.url || image.asset_url),
        )
        .filter((url): url is string => !!url),
    ),
  ];
}

export function galleryIndex(index: number, count: number): number {
  return Math.max(
    0,
    Math.min(Number.isFinite(index) ? Math.trunc(index) : 0, Math.max(0, count - 1)),
  );
}

export function galleryKeyIndex(index: number, count: number, key: string): number | undefined {
  if (key === 'ArrowLeft') return galleryIndex(index - 1, count);
  if (key === 'ArrowRight') return galleryIndex(index + 1, count);
  if (key === 'Home') return 0;
  if (key === 'End') return galleryIndex(count - 1, count);
}
