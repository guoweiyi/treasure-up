import test from 'node:test';
import assert from 'node:assert/strict';
import { commentGalleryImages, galleryIndex, galleryKeyIndex } from './commentGallery.ts';

test('comment gallery keeps only saved attachments and deduplicates without changing order', () => {
  assert.deepEqual(
    commentGalleryImages([
      '/api/v1/assets/first',
      { asset_url: '/api/v1/assets/second' },
      { url: '/api/v1/assets/first' },
      'https://outside.test/photo',
      'javascript:alert(1)',
      '/api/v1/assets/id?token=secret',
      '/api/v1/assets/../admin',
      {},
    ]),
    ['/api/v1/assets/first', '/api/v1/assets/second'],
  );
});

test('gallery keyboard moves within the same attachment list and respects both boundaries', () => {
  let current = galleryIndex(1, 3);
  current = galleryKeyIndex(current, 3, 'ArrowRight');
  assert.equal(current, 2);
  assert.equal(galleryKeyIndex(current, 3, 'ArrowRight'), 2);
  assert.equal(galleryKeyIndex(current, 3, 'Home'), 0);
  assert.equal(galleryKeyIndex(0, 3, 'ArrowLeft'), 0);
  assert.equal(galleryKeyIndex(0, 3, 'End'), 2);
  assert.equal(galleryKeyIndex(0, 3, 'Tab'), undefined);
  assert.equal(galleryKeyIndex(0, 3, 'Escape'), undefined);
  assert.equal(galleryIndex(current, 1), 0);
  assert.equal(galleryIndex(Number.NaN, 2), 0);
  assert.equal(galleryIndex(4, 0), 0);
});
