import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, searchQuery } from './api.ts';
import {
  attachmentProblem,
  conversationUsage,
  failureNextStep,
  formatBytes,
  formatCost,
  parentPath,
  shiftMonth,
  snippetSegments,
  turnUsageText,
} from './everyday.ts';
import { applyEvent, emptyTranscript } from './transcript.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('snippet segments mark each highlight and keep the text whole', () => {
  const parts = snippetSegments('…the Quarterly report is ready', [[5, 9], [15, 6]]);
  assert.deepEqual(parts, [
    { text: '…the ', mark: false },
    { text: 'Quarterly', mark: true },
    { text: ' ', mark: false },
    { text: 'report', mark: true },
    { text: ' is ready', mark: false },
  ]);
  assert.equal(parts.map((p) => p.text).join(''), '…the Quarterly report is ready');
});

test('bad, overlapping and out-of-range highlights are clipped, never thrown', () => {
  assert.deepEqual(snippetSegments('abcdef', [[4, 10], [2, 3], [-5, 1], [1, 0], ['x']]), [
    { text: 'ab', mark: false },
    { text: 'cde', mark: true },
    { text: 'f', mark: true },
  ]);
  assert.deepEqual(snippetSegments('<b>hi</b>', []), [{ text: '<b>hi</b>', mark: false }]);
});

test('usage text says "not reported" instead of inventing numbers', () => {
  assert.equal(turnUsageText(null), 'Usage not reported');
  assert.equal(turnUsageText({}), 'Usage not reported');
  assert.equal(turnUsageText({ input_tokens: 1200, output_tokens: 34 }), '1.2k in · 34 out · cost not reported');
  assert.equal(
    turnUsageText({ input_tokens: 3, output_tokens: 2, cache_read_input_tokens: 5000, cost_usd: 0.0012 }),
    '3 in · 2 out · 5.0k cached · $0.0012'
  );
  assert.equal(formatCost(null), 'not reported');
  assert.equal(formatCost(1.5), '$1.50');
});

test('conversation totals count only reported turns', () => {
  const totals = conversationUsage([
    { usage: { input_tokens: 10, output_tokens: 5, cost_usd: 0.25 } },
    { usage: null },
    { usage: { input_tokens: 1, output_tokens: 1 } },
  ]);
  assert.deepEqual(totals, { turns: 3, reported: 2, tokens: 17, cost: 0.25 });
  assert.equal(conversationUsage([{ usage: { input_tokens: 1 } }]).cost, null);
});

test('attachments are checked like the server does', () => {
  assert.equal(attachmentProblem({ name: 'a.png', type: 'image/png', size: 10 }), null);
  assert.equal(attachmentProblem({ name: 'notes.MD', type: '', size: 10 }), null);
  assert.match(attachmentProblem({ name: 'big.png', type: 'image/png', size: 11 * 1024 * 1024 }), /10 MB/);
  assert.match(attachmentProblem({ name: 'big.txt', type: 'text/plain', size: 2 * 1024 * 1024 }), /1 MB/);
  assert.match(attachmentProblem({ name: 'setup.exe', type: 'application/x-msdownload', size: 10 }), /can't be attached/);
  assert.match(attachmentProblem({ name: 'empty.txt', type: 'text/plain', size: 0 }), /empty/);
});

test('small formatting helpers', () => {
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(2048), '2.0 KB');
  assert.equal(formatBytes(null), '');
  assert.equal(parentPath('a/b/c.txt'), 'a/b');
  assert.equal(parentPath('a'), '');
  assert.equal(shiftMonth('2026-01', -1), '2025-12');
  assert.equal(shiftMonth('2026-12', 1), '2027-01');
  assert.match(failureNextStep('engine_stalled'), /stopped responding/);
  assert.equal(failureNextStep('something_new'), null);
});

test('a sent message keeps its attachments and a finished turn its usage', () => {
  let t = emptyTranscript();
  const id = 'ab'.repeat(16);
  t = applyEvent(t, {
    thread_id: 1, seq: 1, type: 'turn.started', turn_id: 1,
    data: { prompt_text: 'look', attachments: [{ id, name: 'cat.png', mime: 'image/png', bytes: 9, kind: 'image' }, { id: '../x' }] },
  });
  assert.deepEqual(t.items[0].attachments, [{ id, name: 'cat.png', mime: 'image/png', bytes: 9, kind: 'image' }]);
  t = applyEvent(t, { thread_id: 1, seq: 2, type: 'turn.completed', turn_id: 1, data: { usage: { input_tokens: 3 } } });
  assert.deepEqual(t.items[1].usage, { input_tokens: 3 });
  t = applyEvent(t, { thread_id: 1, seq: 3, type: 'turn.started', turn_id: 2, data: { prompt_text: 'plain' } });
  t = applyEvent(t, { thread_id: 1, seq: 4, type: 'turn.failed', turn_id: 2, data: { error: 'x' } });
  assert.deepEqual(t.items[2].attachments, []);
  assert.equal(t.items[3].usage, null);
});

test('search query carries only the filters that are set', () => {
  assert.equal(searchQuery('red pears'), '?q=red+pears');
  assert.equal(
    searchQuery('x', { agentId: 4, since: '2026-10-01', includeArchived: true, limit: 10 }),
    '?q=x&agent_id=4&since=2026-10-01&include_archived=true&limit=10'
  );
});

test('an upload is multipart with no JSON content type, and refusals carry the server message', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ detail: { error_code: 'unsupported_type', message: 'setup.exe can\'t be attached.' } }), {
      status: 415, headers: { 'Content-Type': 'application/json' },
    });
  };
  const file = new File(['MZ'], 'setup.exe');
  await assert.rejects(api.uploadAttachment(7, file), (err) => err.status === 415 && err.code === 'unsupported_type' && /can't be attached/.test(err.message));
  assert.match(calls[0].url, /\/threads\/7\/attachments$/);
  assert.ok(calls[0].options.body instanceof FormData);
  assert.equal(calls[0].options.headers['Content-Type'], undefined);
});

test('sendTurn sends attachment ids only when there are some', async () => {
  const bodies = [];
  globalThis.fetch = async (_url, options) => {
    bodies.push(JSON.parse(options.body));
    return new Response(JSON.stringify({ id: 1 }), { status: 202, headers: { 'Content-Type': 'application/json' } });
  };
  await api.sendTurn(1, 'hi');
  await api.sendTurn(1, 'hi', ['a'.repeat(32)]);
  assert.deepEqual(bodies, [{ prompt_text: 'hi' }, { prompt_text: 'hi', attachment_ids: ['a'.repeat(32)] }]);
});
