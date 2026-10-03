import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

export const gradleSha256 = '9c0f7faeeb306cb14e4279a3e084ca6b596894089a0638e68a07c945a32c9e14';

// Tauri 2.12.1 generates integer SDK 37, but Google publishes 37.0 rather
// than the required android-37 package. Use the available stable SDK 36.
// Fail closed if a later template changes: do not silently rewrite new APIs.
export function prepareAndroid(files) {
  const out = { ...files };
  for (const key of ['compileSdk', 'targetSdk']) {
    const pattern = new RegExp(`\\b${key}\\s*=\\s*(\\d+)\\b`, 'g');
    const matches = [...out.app.matchAll(pattern)];
    if (matches.length !== 1 || !['36', '37'].includes(matches[0][1])) {
      throw new Error(`Unexpected Android template ${key}`);
    }
    out.app = out.app.replace(pattern, `${key} = 36`);
  }
  for (const name of ['project', 'buildSrc']) {
    if (!files[name].includes('com.android.tools.build:gradle:9.3.1')) {
      throw new Error(`Unexpected Android Gradle plugin in ${name}`);
    }
  }
  if (!files.project.includes('org.jetbrains.kotlin:kotlin-gradle-plugin:2.2.10')) {
    throw new Error('Unexpected Kotlin Gradle plugin');
  }
  const wrapperUrl = String.raw`distributionUrl=https\://services.gradle.org/distributions/gradle-9.6.1-bin.zip`;
  if (!files.wrapper.split(/\r?\n/).includes(wrapperUrl)) {
    throw new Error('Unexpected Gradle wrapper distribution');
  }
  const checksums = [...files.wrapper.matchAll(/^distributionSha256Sum=(.*)$/gm)];
  if (checksums.length > 1 || (checksums.length === 1 && checksums[0][1].trim() !== gradleSha256)) {
    throw new Error('Unexpected Gradle wrapper checksum');
  }
  if (!checksums.length) out.wrapper = files.wrapper.trimEnd() + `\ndistributionSha256Sum=${gradleSha256}\n`;
  return out;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = new URL('../src-tauri/gen/android/', import.meta.url);
  const paths = { app: 'app/build.gradle.kts', project: 'build.gradle.kts', buildSrc: 'buildSrc/build.gradle.kts', wrapper: 'gradle/wrapper/gradle-wrapper.properties' };
  const files = Object.fromEntries(await Promise.all(Object.entries(paths).map(async ([name, path]) => [name, await readFile(new URL(path, root), 'utf8')])));
  const prepared = prepareAndroid(files);
  // Validate the complete template before any mutation.
  for (const name of ['app', 'wrapper']) await writeFile(new URL(paths[name], root), prepared[name]);
  console.log('Android SDK 36; AGP 9.3.1; Gradle 9.6.1 with verified SHA-256; Kotlin 2.2.10');
}
