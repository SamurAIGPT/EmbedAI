// A small QR code encoder for pairing links: byte mode, error correction
// level M, versions 1 to 6 (up to 106 bytes), no dependencies. Follows
// ISO/IEC 18004; anything longer returns null and the UI shows the text only.

const MAX_VERSION = 6;
// Level M, indexed by version (index 0 unused).
const ECC_PER_BLOCK = [0, 10, 16, 26, 18, 24, 16];
const BLOCKS = [0, 1, 1, 1, 2, 2, 4];
const FORMAT_LEVEL_M = 0; // the two format bits for level M

export type QrMatrix = boolean[][]; // [row][column], true = dark

function rawDataModules(version: number): number {
  let result = (16 * version + 128) * version + 64;
  if (version >= 2) {
    const align = Math.floor(version / 7) + 2;
    result -= (25 * align - 10) * align - 55;
  }
  return result;
}

function dataCodewords(version: number): number {
  return Math.floor(rawDataModules(version) / 8) - ECC_PER_BLOCK[version] * BLOCKS[version];
}

function alignmentPositions(version: number): number[] {
  if (version === 1) return [];
  const count = Math.floor(version / 7) + 2;
  const size = version * 4 + 17;
  const step = Math.ceil((version * 4 + 4) / (count * 2 - 2)) * 2;
  const result = [6];
  for (let pos = size - 7; result.length < count; pos -= step) result.splice(1, 0, pos);
  return result;
}

function gfMultiply(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xff;
}

function rsDivisor(degree: number): number[] {
  const result = new Array<number>(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      result[j] = gfMultiply(result[j], root);
      if (j + 1 < degree) result[j] ^= result[j + 1];
    }
    root = gfMultiply(root, 0x02);
  }
  return result;
}

function rsRemainder(data: number[], divisor: number[]): number[] {
  const result = new Array<number>(divisor.length).fill(0);
  for (const byte of data) {
    const factor = byte ^ (result.shift() as number);
    result.push(0);
    divisor.forEach((coefficient, i) => { result[i] ^= gfMultiply(coefficient, factor); });
  }
  return result;
}

function encodeData(bytes: Uint8Array, version: number): number[] | null {
  const capacityBits = dataCodewords(version) * 8;
  const bits: number[] = [];
  const push = (value: number, length: number) => {
    for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4); // byte mode
  push(bytes.length, 8); // count field is 8 bits for versions 1 to 9
  for (const b of bytes) push(b, 8);
  if (bits.length > capacityBits) return null;
  push(0, Math.min(4, capacityBits - bits.length));
  push(0, (8 - (bits.length % 8)) % 8);
  for (let pad = 0xec; bits.length < capacityBits; pad ^= 0xec ^ 0x11) push(pad, 8);
  const codewords: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    codewords.push(bits.slice(i, i + 8).reduce((acc, bit) => (acc << 1) | bit, 0));
  }
  return codewords;
}

function interleave(data: number[], version: number): number[] {
  const blocks = BLOCKS[version];
  const eccLength = ECC_PER_BLOCK[version];
  const raw = Math.floor(rawDataModules(version) / 8);
  const shortBlocks = blocks - (raw % blocks);
  const shortLength = Math.floor(raw / blocks);
  const divisor = rsDivisor(eccLength);
  const all: number[][] = [];
  for (let i = 0, k = 0; i < blocks; i++) {
    const chunk = data.slice(k, k + shortLength - eccLength + (i < shortBlocks ? 0 : 1));
    k += chunk.length;
    const ecc = rsRemainder(chunk, divisor);
    if (i < shortBlocks) chunk.push(0);
    all.push(chunk.concat(ecc));
  }
  const result: number[] = [];
  for (let i = 0; i < all[0].length; i++) {
    all.forEach((block, j) => {
      if (i !== shortLength - eccLength || j >= shortBlocks) result.push(block[i]);
    });
  }
  return result;
}

const MASKS: Array<(x: number, y: number) => boolean> = [
  (x, y) => (x + y) % 2 === 0,
  (_x, y) => y % 2 === 0,
  (x) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
  (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
  (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
];

function formatBits(mask: number): number {
  const data = (FORMAT_LEVEL_M << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

class Grid {
  readonly size: number;
  readonly dark: boolean[][];
  readonly reserved: boolean[][];

  constructor(size: number) {
    this.size = size;
    this.dark = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
    this.reserved = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  }

  fixed(x: number, y: number, dark: boolean) {
    this.dark[y][x] = dark;
    this.reserved[y][x] = true;
  }

  drawFormat(mask: number) {
    const bits = formatBits(mask);
    const bit = (i: number) => ((bits >>> i) & 1) !== 0;
    const s = this.size;
    for (let i = 0; i <= 5; i++) this.fixed(8, i, bit(i));
    this.fixed(8, 7, bit(6));
    this.fixed(8, 8, bit(7));
    this.fixed(7, 8, bit(8));
    for (let i = 9; i < 15; i++) this.fixed(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) this.fixed(s - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i++) this.fixed(8, s - 15 + i, bit(i));
    this.fixed(8, s - 8, true); // the dark module
  }
}

function functionPatterns(version: number): Grid {
  const size = version * 4 + 17;
  const grid = new Grid(size);
  for (let i = 0; i < size; i++) {
    grid.fixed(6, i, i % 2 === 0);
    grid.fixed(i, 6, i % 2 === 0);
  }
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const x = cx + dx;
        const y = cy + dy;
        if (x < 0 || y < 0 || x >= size || y >= size) continue;
        const distance = Math.max(Math.abs(dx), Math.abs(dy));
        grid.fixed(x, y, distance !== 2 && distance !== 4);
      }
    }
  }
  const positions = alignmentPositions(version);
  const last = positions.length - 1;
  positions.forEach((cx, i) => {
    positions.forEach((cy, j) => {
      if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) return;
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) grid.fixed(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    });
  });
  grid.drawFormat(0); // reserves the format areas; redrawn with the chosen mask
  return grid;
}

function placeCodewords(grid: Grid, codewords: number[]) {
  const size = grid.size;
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vertical = 0; vertical < size; vertical++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? size - 1 - vertical : vertical;
        if (grid.reserved[y][x] || i >= codewords.length * 8) continue;
        grid.dark[y][x] = ((codewords[i >>> 3] >>> (7 - (i & 7))) & 1) !== 0;
        i++;
      }
    }
  }
}

function applyMask(grid: Grid, mask: number) {
  for (let y = 0; y < grid.size; y++) {
    for (let x = 0; x < grid.size; x++) {
      if (!grid.reserved[y][x] && MASKS[mask](x, y)) grid.dark[y][x] = !grid.dark[y][x];
    }
  }
}

// Runs of five or more, 2x2 blocks and dark/light balance (the finder-like
// pattern rule is left out: any mask decodes, this only picks a clearer one).
function penalty(dark: boolean[][]): number {
  const size = dark.length;
  let score = 0;
  for (let a = 0; a < size; a++) {
    let rowRun = 1;
    let colRun = 1;
    for (let b = 1; b < size; b++) {
      if (dark[a][b] === dark[a][b - 1]) rowRun++;
      else { if (rowRun >= 5) score += rowRun - 2; rowRun = 1; }
      if (dark[b][a] === dark[b - 1][a]) colRun++;
      else { if (colRun >= 5) score += colRun - 2; colRun = 1; }
    }
    if (rowRun >= 5) score += rowRun - 2;
    if (colRun >= 5) score += colRun - 2;
  }
  let total = 0;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (dark[y][x]) total++;
      if (x + 1 < size && y + 1 < size) {
        const c = dark[y][x];
        if (c === dark[y][x + 1] && c === dark[y + 1][x] && c === dark[y + 1][x + 1]) score += 3;
      }
    }
  }
  const cells = size * size;
  score += Math.floor(Math.abs(total * 20 - cells * 10) / cells) * 10;
  return score;
}

export function encodeQr(text: string): QrMatrix | null {
  const bytes = new TextEncoder().encode(text);
  for (let version = 1; version <= MAX_VERSION; version++) {
    const data = encodeData(bytes, version);
    if (!data) continue;
    const codewords = interleave(data, version);
    let best: { score: number; dark: boolean[][] } | null = null;
    for (let mask = 0; mask < MASKS.length; mask++) {
      const grid = functionPatterns(version);
      placeCodewords(grid, codewords);
      applyMask(grid, mask);
      grid.drawFormat(mask);
      const score = penalty(grid.dark);
      if (!best || score < best.score) best = { score, dark: grid.dark };
    }
    return best ? best.dark : null;
  }
  return null;
}

// One SVG path, four modules of quiet zone, scaled by the viewBox.
// Exposed for tests only.
export const internals = { rsDivisor, rsRemainder, formatBits, dataCodewords, alignmentPositions };

export function qrSvgPath(matrix: QrMatrix): { path: string; size: number } {
  const quiet = 4;
  const parts: string[] = [];
  matrix.forEach((row, y) => {
    row.forEach((dark, x) => {
      if (dark) parts.push(`M${x + quiet} ${y + quiet}h1v1h-1z`);
    });
  });
  return { path: parts.join(""), size: matrix.length + quiet * 2 };
}
