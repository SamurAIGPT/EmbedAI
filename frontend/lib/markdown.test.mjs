import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseInline, parseMarkdown, safeHref } from './markdown.ts';

const kinds = (blocks) => blocks.map((b) => b.type);

test('paragraphs, headings and rules', () => {
  const blocks = parseMarkdown('# Title\n\nHello\nworld\n\n---\n\n### Sub');
  assert.deepEqual(kinds(blocks), ['heading', 'paragraph', 'rule', 'heading']);
  assert.equal(blocks[0].level, 1);
  assert.equal(blocks[3].level, 3);
});

test('a fenced code block keeps its text and language, and may be unclosed while streaming', () => {
  const closed = parseMarkdown('```js\nconst a = 1;\n# not a heading\n```\nafter');
  assert.deepEqual(kinds(closed), ['code', 'paragraph']);
  assert.equal(closed[0].lang, 'js');
  assert.equal(closed[0].text, 'const a = 1;\n# not a heading');
  assert.equal(closed[0].closed, true);
  const open = parseMarkdown('```py\nprint(1)');
  assert.equal(open[0].closed, false);
  assert.equal(open[0].text, 'print(1)');
});

test('lists, ordered lists and quotes', () => {
  const [ul] = parseMarkdown('- a\n- b\n  more\n- c');
  assert.equal(ul.type, 'list');
  assert.equal(ul.ordered, false);
  assert.equal(ul.items.length, 3);
  const [ol] = parseMarkdown('3. x\n4. y');
  assert.equal(ol.ordered, true);
  assert.equal(ol.start, 3);
  const [q] = parseMarkdown('> quoted\n> text');
  assert.equal(q.type, 'quote');
  assert.equal(q.blocks[0].type, 'paragraph');
});

test('tables need a delimiter row and pad short rows', () => {
  const [t] = parseMarkdown('| a | b |\n|:--|--:|\n| 1 |\n| 2 | 3 |');
  assert.equal(t.type, 'table');
  assert.deepEqual(t.align, ['left', 'right']);
  assert.equal(t.rows.length, 2);
  assert.equal(t.rows[0].length, 2);
  assert.deepEqual(kinds(parseMarkdown('a | b\nplain')), ['paragraph']);
});

test('inline code, emphasis and strike', () => {
  const nodes = parseInline('a **b** _c_ ~~d~~ `e*f*`');
  assert.deepEqual(nodes.map((n) => n.type), ['text', 'strong', 'text', 'em', 'text', 'del', 'text', 'code']);
  assert.equal(nodes.at(-1).text, 'e*f*');
});

test('snake_case and unmatched markers stay literal', () => {
  assert.deepEqual(parseInline('use my_var_name here'), [{ type: 'text', text: 'use my_var_name here' }]);
  assert.deepEqual(parseInline('half **open'), [{ type: 'text', text: 'half **open' }]);
  assert.deepEqual(parseInline('2 * 3 * 4'), [{ type: 'text', text: '2 * 3 * 4' }]);
});

test('links: only http, https and mailto become clickable', () => {
  const ok = parseInline('[site](https://example.com/a)');
  assert.equal(ok[0].type, 'link');
  assert.equal(ok[0].href, 'https://example.com/a');
  const bad = parseInline('[x](javascript:alert(1))');
  assert.ok(bad.every((n) => n.type !== 'link'));
  assert.equal(safeHref('data:text/html,hi'), null);
  assert.equal(safeHref('/relative'), null);
  assert.equal(safeHref('mailto:a@b.co'), 'mailto:a@b.co');
  const bare = parseInline('see https://example.com/x.');
  assert.equal(bare[1].href, 'https://example.com/x');
});

test('HTML in an answer stays plain text', () => {
  const [p] = parseMarkdown('<script>alert(1)</script> <img src=x onerror=y>');
  assert.equal(p.type, 'paragraph');
  assert.ok(p.children.every((n) => n.type === 'text'));
});

test('re-parsing every prefix of a streamed answer never throws', () => {
  const full = '# Hi\n\n- a **b\n- c `d\n\n```ts\nlet x\n\n| a | b |\n|--|--|\n| [l](http://x';
  for (let n = 0; n <= full.length; n += 1) parseMarkdown(full.slice(0, n));
});
