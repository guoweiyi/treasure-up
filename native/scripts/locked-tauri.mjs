import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Keep custom CLI options before the Cargo delimiter on every platform.
const args = process.argv.slice(2);
const delimiter = args.indexOf('--');
if (delimiter < 0) args.push('--');
if (!args.slice(delimiter < 0 ? args.length : delimiter + 1).includes('--locked')) args.push('--locked');
const result = spawnSync(process.execPath, [fileURLToPath(new URL('../node_modules/@tauri-apps/cli/tauri.js', import.meta.url)), ...args], { stdio: 'inherit' });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
