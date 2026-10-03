// Public dependency metadata only. This makes the CI-produced lock recoverable
// through authenticated job-log readers when binary artifact transport is unavailable.
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const lock = await readFile(new URL('../src-tauri/Cargo.lock', import.meta.url));
const hash = createHash('sha256').update(lock).digest('hex');
console.log(`TREASURE_CARGO_LOCK_BEGIN sha256=${hash} bytes=${lock.length}`);
for (const chunk of lock.toString('base64').match(/.{1,4096}/g) || []) console.log(chunk);
console.log('TREASURE_CARGO_LOCK_END');
