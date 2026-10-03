import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareAndroid, gradleSha256 } from '../scripts/prepare-android.mjs';

const fixture = () => ({
  app: 'android {\n  compileSdk = 37\n  defaultConfig { targetSdk = 37; minSdk = 26 }\n}',
  project: 'classpath("com.android.tools.build:gradle:9.3.1")\nclasspath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.2.10")',
  buildSrc: 'implementation("com.android.tools.build:gradle:9.3.1")',
  wrapper: String.raw`distributionUrl=https\://services.gradle.org/distributions/gradle-9.6.1-bin.zip` + '\n'
});

test('generated Android template gets stable SDK and verified wrapper checksum idempotently', () => {
  const source = fixture();
  const result = prepareAndroid(source);
  assert.match(result.app, /compileSdk = 36/);
  assert.match(result.app, /targetSdk = 36/);
  assert.match(result.app, /minSdk = 26/);
  assert.ok(result.wrapper.includes(`distributionSha256Sum=${gradleSha256}`));
  assert.deepEqual(prepareAndroid(result), result);
  assert.match(source.app, /compileSdk = 37/);
});

test('unexpected generated toolchain fails instead of silently accepting drift', () => {
  for (const [key, value] of [
    ['app', fixture().app.replace('compileSdk = 37', 'compileSdk = 38')],
    ['app', fixture().app + '\ncompileSdk = 37'],
    ['project', fixture().project.replace('9.3.1', '9.4.1')],
    ['buildSrc', fixture().buildSrc.replace('9.3.1', '9.4.1')],
    ['wrapper', fixture().wrapper.replace('9.6.1', '9.8.0')],
    ['wrapper', fixture().wrapper + 'distributionSha256Sum=invalid\n']
  ]) assert.throws(() => prepareAndroid({ ...fixture(), [key]: value }));
});
