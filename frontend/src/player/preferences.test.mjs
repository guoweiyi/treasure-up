import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readPreferences, defaultPreferences, includeAtDensity } from './preferences.ts';

test('malformed or old browser storage cannot break the player', () => {
  for (const raw of ['not json', 'null', '[]', '42'])
    assert.deepEqual(readPreferences(raw, false), defaultPreferences(false));
  const migrated = readPreferences(JSON.stringify({ fontSize: 35, visible: false }));
  assert.equal(migrated.fontSize, 35);
  assert.equal(migrated.visible, false);
  assert.equal(migrated.density, 100);
  assert.equal(migrated.offset, 0);
});

test('out of range and invalid display values have safe bounds', () => {
  const value = readPreferences(
    '{"opacity":-15,"fontSize":9999,"offset":-999,"density":1000,"area":1,"speed":0,"color":"url(x)","fontFamily":"unknown","outline":"invalid","weight":950,"visible":"false","strokeWidth":null,"spacing":1e999}',
  );
  assert.equal(value.opacity, 0);
  assert.equal(value.fontSize, 120);
  assert.equal(value.offset, -60);
  assert.equal(value.density, 100);
  assert.equal(value.area, 25);
  assert.equal(value.speed, 1);
  assert.equal(value.color, '#ffffff');
  assert.equal(value.fontFamily, 'Microsoft YaHei');
  assert.equal(value.outline, 'stroke');
  assert.equal(value.weight, 400);
  assert.equal(value.visible, true);
  assert.equal(value.strokeWidth, 1);
  assert.equal(value.spacing, 0);
});

test('density is reproducible and never changes the input archive', () => {
  const indices = Array.from({ length: 100 }, (_, index) => index);
  for (const density of [10, 30, 50, 100]) {
    const first = indices.filter((index) => includeAtDensity(index, density));
    assert.equal(first.length, density);
    assert.deepEqual(
      indices.filter((index) => includeAtDensity(index, density)),
      first,
    );
  }
  assert.equal(indices.length, 100);
});
