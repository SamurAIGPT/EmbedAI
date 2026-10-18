import assert from 'node:assert/strict';
import { test } from 'node:test';
import { encodeQr, internals, qrSvgPath } from './qr.ts';

test('Reed-Solomon matches the published version 1-M example', () => {
  // "HELLO WORLD" at 1-M, data and error correction codewords from the standard's worked example.
  const data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17];
  const ecc = internals.rsRemainder(data, internals.rsDivisor(10));
  assert.deepEqual(ecc, [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]);
});

test('format bits for level M', () => {
  assert.equal(internals.formatBits(0).toString(2).padStart(15, '0'), '101010000010010');
  assert.equal(internals.formatBits(5).toString(2).padStart(15, '0'), '100000011001110');
  assert.equal(internals.formatBits(7).toString(2).padStart(15, '0'), '100101010100000');
});

test('capacities and alignment positions for versions 1 to 6', () => {
  assert.deepEqual([1, 2, 3, 4, 5, 6].map(internals.dataCodewords), [16, 28, 44, 64, 86, 108]);
  assert.deepEqual(internals.alignmentPositions(1), []);
  assert.deepEqual(internals.alignmentPositions(2), [6, 18]);
  assert.deepEqual(internals.alignmentPositions(6), [6, 34]);
});

test('a pairing link fits, has finder patterns and the dark module', () => {
  const matrix = encodeQr('https://dots.example.com/pair#code=ABCD-EFGH');
  assert.ok(matrix);
  const size = matrix.length;
  assert.equal((size - 17) % 4, 0);
  assert.ok(size <= 41);
  for (const [x, y] of [[0, 0], [size - 7, 0], [0, size - 7]]) {
    for (let i = 0; i < 7; i++) {
      assert.equal(matrix[y][x + i], true);
      assert.equal(matrix[y + 6][x + i], true);
      assert.equal(matrix[y + i][x], true);
    }
    assert.equal(matrix[y + 1][x + 1], false);
    assert.equal(matrix[y + 3][x + 3], true);
  }
  assert.equal(matrix[size - 8][8], true);
  // The two copies of the format bits agree.
  const first = [0, 1, 2, 3, 4, 5, 7, 8].map((y) => matrix[y][8]);
  const second = [0, 1, 2, 3, 4, 5, 6, 7].map((i) => matrix[8][size - 1 - i]);
  assert.deepEqual(first, second);
});

test('too long for version 6 returns null; the SVG path has a quiet zone', () => {
  assert.equal(encodeQr('x'.repeat(200)), null);
  const { path, size } = qrSvgPath(encodeQr('hi'));
  assert.equal(size, 21 + 8);
  assert.ok(path.startsWith('M4 4h1v1h-1z'));
});
