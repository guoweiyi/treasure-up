import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { danmakuLayout } from './layout.ts';

// Execute the pinned dependency's actual layout worker in a JS realm (no
// browser, DOM or network). This guards our CSS translation/collision contract
// when the plugin is upgraded instead of duplicating its packing algorithm.
const bundle = readFileSync(
  new URL(
    '../../node_modules/artplayer-plugin-danmuku/dist/artplayer-plugin-danmuku.js',
    import.meta.url,
  ),
  'utf8',
);
const workerLiteral = bundle.match(/const n=('(?:\\.|[^'\\])*'),s=/)?.[1];
assert.ok(workerLiteral, 'Review the danmaku layout integration after changing plugin builds');
const worker = runInNewContext(workerLiteral, {}, { timeout: 1000 });
let result;
const context = {
  postMessage: (value) => {
    result = value.result;
  },
};
runInNewContext(worker, context, { timeout: 1000 });

test('real plugin worker keeps fixed bottom rows anchored across region sizes and avoids collisions', () => {
  assert.ok(
    bundle.includes('.$ref.offsetTop'),
    'Lane bookkeeping must use pre-translation coordinates',
  );
  for (const [width, height, subtitleSafe] of [
    [800, 450, false],
    [800, 600, false],
    [1920, 1080, true],
  ]) {
    const baselines = [];
    for (const area of [25, 50, 100]) {
      const layout = danmakuLayout(width, height, 1920, 1080, area, subtitleSafe);
      const visibles = [];
      for (let lane = 0; lane < 2; lane++) {
        context.onmessage({
          data: {
            id: lane + 1,
            type: 'getDanmuTop',
            target: { mode: 2, height: 28, speed: 100 },
            visibles,
            clientWidth: width,
            clientHeight: height,
            marginTop: layout.margin[0],
            marginBottom: layout.margin[1],
            antiOverlap: true,
          },
        });
        assert.equal(typeof result, 'number');
        if (!lane) baselines.push(result + 28 + layout.bottomOffset);
        else assert.ok(result + 28 <= visibles[0].top);
        visibles.push({
          mode: 2,
          top: result,
          height: 28,
          width: 150,
          left: 0,
          right: 0,
          speed: 0,
          distance: 0,
          time: 5,
        });
      }
    }
    assert.ok(baselines.every((value) => Math.abs(value - baselines[0]) < 0.001));
  }
});
