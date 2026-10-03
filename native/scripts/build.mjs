import { mkdir, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const root = new URL('../', import.meta.url);
await mkdir(new URL('dist/', root), { recursive: true });
for (const name of ['index.html', 'style.css', 'app.js', 'policy.js']) {
  await copyFile(new URL(`src/${name}`, root), new URL(`dist/${name}`, root));
}
console.log(`Bundled connection page: ${fileURLToPath(new URL('dist/', root))}`);
