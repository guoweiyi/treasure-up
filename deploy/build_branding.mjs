// Rebuild delivery assets from the approved logo master; never redraw the brand.
// node deploy/build_branding.mjs [path-to-sharp-module]
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url);
const sharp = require(process.argv[2] || 'sharp');
const root = fileURLToPath(new URL('../', import.meta.url));
const brand = resolve(root, 'frontend/public/brand');
const source = resolve(root, 'assets/branding/logo-transparent.png');
const native = resolve(root, 'native/apple/TreasureUp/Assets.xcassets');
await mkdir(brand, { recursive: true });
// The supplied 1254px logo has its wordmark below y=1024. Only the mascot is
// used for application icons, where the full wordmark is too small to read.
const mascot = await sharp(source).extract({ left: 0, top: 0, width: 1254, height: 1024 }).png().toBuffer();
const mark = await sharp(mascot).trim().resize(1024, 1024, { fit: 'contain', background: '#00000000' }).png().toBuffer();
const lockup = await sharp(source).trim().resize(1024, 1024, { fit: 'contain', background: '#00000000' }).png().toBuffer();
await writeFile(resolve(brand, 'logo-mark.png'), mark);
await writeFile(resolve(brand, 'logo-lockup.png'), lockup);
for (const [role, bytes] of [['mark', mark], ['lockup', lockup]]) {
  for (const size of [128, 256, 512]) await sharp(bytes).resize(size, size).png().toFile(resolve(brand, `logo-${role}-${size}.png`));
}
async function appIcon(size, fraction = 0.86) {
  const edge = Math.round(size * fraction);
  const artwork = await sharp(mark).resize(edge, edge).toBuffer();
  return sharp({ create: { width: size, height: size, channels: 3, background: '#ffffff' } })
    .composite([{ input: artwork, gravity: 'centre' }]).removeAlpha().png().toBuffer();
}
for (const size of [192, 512, 1024]) await writeFile(resolve(brand, `app-icon-${size}.png`), await appIcon(size));
// All artwork fits within the central maskable safe circle (r=40% of width).
await writeFile(resolve(brand, 'app-icon-maskable-512.png'), await appIcon(512, 0.56));
await writeFile(resolve(brand, 'apple-touch-icon.png'), await appIcon(180));
await writeFile(resolve(brand, 'favicon-32.png'), await appIcon(32, 0.94));
const icons = await Promise.all([16, 32, 48].map((size) => appIcon(size, 0.94)));
const icoHeader = Buffer.alloc(6 + icons.length * 16);
icoHeader.writeUInt16LE(1, 2); icoHeader.writeUInt16LE(icons.length, 4);
let offset = icoHeader.length;
for (const [i, bytes] of icons.entries()) {
  const entry = 6 + i * 16, size = [16, 32, 48][i];
  icoHeader[entry] = size; icoHeader[entry + 1] = size;
  icoHeader.writeUInt16LE(1, entry + 4); icoHeader.writeUInt16LE(32, entry + 6);
  icoHeader.writeUInt32LE(bytes.length, entry + 8); icoHeader.writeUInt32LE(offset, entry + 12);
  offset += bytes.length;
}
await writeFile(resolve(root, 'frontend/public/favicon.ico'), Buffer.concat([icoHeader, ...icons]));
// Preserve the old URL for pinned tabs and existing installations.
const png192 = await readFile(resolve(brand, 'app-icon-192.png'));
await writeFile(resolve(root, 'frontend/public/app-icon.svg'), `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><title>Treasure Up</title><image width="192" height="192" href="data:image/png;base64,${png192.toString('base64')}"/></svg>\n`);
const socialLogo = await sharp(lockup).resize(500, 500).toBuffer();
await sharp({ create: { width: 1200, height: 630, channels: 3, background: '#edf3ff' } })
  .composite([{ input: socialLogo, gravity: 'centre' }]).removeAlpha().png().toFile(resolve(brand, 'social-card.png'));
for (const [folder, name, bytes] of [['BrandMark.imageset', 'brand-mark.png', mark], ['BrandLockup.imageset', 'brand-lockup.png', lockup]]) {
  await mkdir(resolve(native, folder), { recursive: true });
  await writeFile(resolve(native, folder, name), bytes);
}
await copyFile(resolve(brand, 'app-icon-1024.png'), resolve(native, 'AppIcon.appiconset/AppIcon.png'));
// Self-contained userscript branding works before a server is configured and
// never sends logo requests to the user's private server from a Bilibili page.
const small = await sharp(mark).resize(96, 96).png().toBuffer();
const dataURL = `data:image/png;base64,${small.toString('base64')}`;
const scriptPath = resolve(root, 'frontend/public/userscripts/treasure-up.user.js');
let script = await readFile(scriptPath, 'utf8');
script = script.replace(/^\/\/ @icon[^\n]*\n/m, '').replace(/(\/\/ @author[^\n]*\n)/, `$1// @icon         ${dataURL}\n`);
if (/const BRAND_MARK = /.test(script)) script = script.replace(/const BRAND_MARK = '[^']*';/, `const BRAND_MARK = '${dataURL}';`);
else script = script.replace("  const STORAGE = 'treasure-up:collector:v1';", `  const BRAND_MARK = '${dataURL}';\n  const STORAGE = 'treasure-up:collector:v1';`);
await writeFile(scriptPath, script);
const files = ['logo-mark.png', 'logo-lockup.png', ...['mark', 'lockup'].flatMap(role => [128, 256, 512].map(size => `logo-${role}-${size}.png`)), 'app-icon-192.png', 'app-icon-512.png', 'app-icon-1024.png', 'app-icon-maskable-512.png', 'apple-touch-icon.png', 'favicon-32.png', 'social-card.png'];
const entries = {};
for (const name of files) {
  const bytes = await readFile(resolve(brand, name));
  const { width, height, hasAlpha } = await sharp(bytes).metadata();
  entries[name] = { width, height, alpha: hasAlpha, sha256: createHash('sha256').update(bytes).digest('hex') };
}
await writeFile(resolve(brand, 'assets.json'), JSON.stringify({ source: 'assets/branding/logo-original.png', master: 'assets/branding/logo-transparent.png', assets: entries }, null, 2) + '\n');
console.log('Brand assets generated for web, installable PWA, userscript and iOS.');
