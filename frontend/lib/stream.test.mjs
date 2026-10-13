import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, ApiError, createSseParser } from './api.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function frame(type, data = {}, seq = 1) {
  const envelope = { run_id: 1, seq, type, data, ts: '2026-01-01T00:00:00+00:00' };
  return `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify(envelope)}\n\n`;
}

function collect() {
  const events = [];
  return { events, parser: createSseParser(event => events.push(event)) };
}

function streamOf(chunks, { failAfter } = {}) {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (failAfter !== undefined && index >= failAfter) {
        controller.error(new TypeError('network error'));
        return;
      }
      if (index >= chunks.length) {
        controller.close();
        return;
      }
      const chunk = chunks[index++];
      controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk);
    },
  });
}

function respondWith(body, init = {}) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' }, ...init });
  };
  return calls;
}

test('parses whole frames', () => {
  const { events, parser } = collect();
  parser.push(frame('turn.started') + frame('text.delta', { text: 'hi', mode: 'append' }, 2));
  assert.deepEqual(events.map(e => e.type), ['turn.started', 'text.delta']);
  assert.equal(events[1].data.text, 'hi');
});

test('parses a frame delivered in arbitrary pieces', () => {
  const { events, parser } = collect();
  const whole = frame('text.delta', { text: 'héllo ✓', mode: 'append' });
  for (let i = 0; i < whole.length; i += 7) parser.push(whole.slice(i, i + 7));
  assert.equal(events.length, 1);
  assert.equal(events[0].data.text, 'héllo ✓');
});

test('handles CRLF line endings, even split across chunks', () => {
  const { events, parser } = collect();
  const crlf = frame('text.delta', { text: 'a', mode: 'append' }).replace(/\n/g, '\r\n');
  const cut = crlf.indexOf('\r\n\r\n') + 1; // between the \r and \n of a line end
  parser.push(crlf.slice(0, cut));
  parser.push(crlf.slice(cut));
  assert.equal(events.length, 1);
  assert.equal(events[0].data.text, 'a');
});

test('ignores keep-alive comments and blank frames', () => {
  const { events, parser } = collect();
  parser.push(': keepalive\n\n');
  parser.push('\n\n');
  parser.push(frame('turn.started'));
  parser.push(': keepalive\n\n');
  assert.deepEqual(events.map(e => e.type), ['turn.started']);
});

test('skips frames that are malformed instead of failing', () => {
  const { events, parser } = collect();
  parser.push('data: not json\n\n');
  parser.push('data: [1,2]\n\n');
  parser.push('data: {"type": 5, "data": {}}\n\n');
  parser.push('data: {"type": "x"}\n\n');
  parser.push('event: only-a-name\n\n');
  parser.push(frame('turn.completed', { status: 'succeeded', result: 'ok' }));
  assert.deepEqual(events.map(e => e.type), ['turn.completed']);
});

test('joins multi-line data fields with a newline, as the SSE format defines', () => {
  const { events, parser } = collect();
  const json = JSON.stringify({ run_id: 1, seq: 1, type: 'x', data: {}, ts: 't' });
  // Cut after a comma, where a newline is just whitespace to JSON.
  const cut = json.indexOf(',') + 1;
  parser.push(`data: ${json.slice(0, cut)}\ndata: ${json.slice(cut)}\n\n`);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'x');
});

test('streamLocalTask delivers events in order and resolves when the stream ends', async () => {
  const calls = respondWith(streamOf([
    frame('turn.started'),
    frame('text.delta', { text: 'a', mode: 'append' }, 2),
    frame('text.delta', { text: 'b', mode: 'append' }, 3),
    frame('turn.completed', { status: 'succeeded', result: 'ab' }, 4),
  ]));
  const events = [];
  await api.streamLocalTask(7, event => events.push(event));
  assert.deepEqual(events.map(e => e.type), ['turn.started', 'text.delta', 'text.delta', 'turn.completed']);
  assert.match(calls[0].url, /\/local\/tasks\/7\/events$/);
  assert.equal(calls[0].options.headers.Accept, 'text/event-stream');
  assert.equal('Content-Type' in calls[0].options.headers, false);
});

test('streamLocalTask copes with a multi-byte character split across reads', async () => {
  const bytes = new TextEncoder().encode(frame('text.delta', { text: '✓', mode: 'append' }));
  const cut = bytes.indexOf(0xe2) + 1;
  respondWith(streamOf([bytes.slice(0, cut), bytes.slice(cut)]));
  const events = [];
  await api.streamLocalTask(1, event => events.push(event));
  assert.equal(events[0].data.text, '✓');
});

test('a stream that ends without a terminal event simply ends; the caller must check the task', async () => {
  respondWith(streamOf([frame('turn.started'), frame('text.delta', { text: 'par', mode: 'append' }, 2)]));
  const events = [];
  await api.streamLocalTask(1, event => events.push(event));
  assert.equal(events.some(e => e.type === 'turn.completed' || e.type === 'turn.failed'), false);
});

test('a dropped connection rejects instead of resolving as a success', async () => {
  respondWith(streamOf([frame('turn.started')], { failAfter: 1 }));
  await assert.rejects(api.streamLocalTask(1, () => {}), /network error/);
});

test('an HTTP error is reported with its status', async () => {
  respondWith('nope', { status: 403, statusText: 'Forbidden' });
  await assert.rejects(api.streamLocalTask(1, () => {}), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 403);
    return true;
  });
});

test('the abort signal is passed to fetch', async () => {
  const calls = respondWith(streamOf([]));
  const controller = new AbortController();
  await api.streamLocalTask(1, () => {}, controller.signal);
  assert.equal(calls[0].options.signal, controller.signal);
});
