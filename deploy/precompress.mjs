// Precompress only public, content-hashed application bundles; never API/media data.
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';

const root = process.argv[2];
if (!root) throw new Error('Usage: node precompress.mjs <dist/assets>');
let original = 0;
let compressed = 0;
for (const item of await readdir(root, { withFileTypes: true })) {
  if (!item.isFile() || !/\.(?:js|css)$/.test(item.name)) continue;
  const path = join(root, item.name);
  const source = await readFile(path);
  const packed = gzipSync(source, { level: 9 });
  if (packed.length >= source.length) continue;
  await writeFile(`${path}.gz`, packed);
  original += source.length;
  compressed += packed.length;
}
console.log(`Precompressed bundles: ${original} -> ${compressed} bytes`);
