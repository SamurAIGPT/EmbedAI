import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, ApiError } from './api.ts';
import { applyEvent, emptyTranscript } from './transcript.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function stored(seq, type, data = {}, turn_id = 1) {
  const envelope = { thread_id: 1, seq, type, turn_id, data, ts: '2026-01-01T00:00:00+00:00' };
  return `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify(envelope)}\n\n`;
}

function typing(text, mode = 'append') {
  const envelope = { thread_id: 1, type: 'text.delta', turn_id: 1, data: { text, mode } };
  return `event: text.delta\ndata: ${JSON.stringify(envelope)}\n\n`;
}

function respondWith(body, init = {}) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' }, ...init });
  };
  return calls;
}

function respondJson(status, body) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  };
  return calls;
}

test('streamThread asks for the stream of that thread and, from the start, sends no Last-Event-ID', async () => {
  const calls = respondWith('');
  await api.streamThread(3, null, () => {});
  assert.match(calls[0].url, /\/threads\/3\/events\/stream$/);
  assert.equal(calls[0].options.headers['Last-Event-ID'], undefined);
  assert.equal(calls[0].options.headers.Accept, 'text/event-stream');
});

test('streamThread resumes after the last event seen with Last-Event-ID', async () => {
  const calls = respondWith('');
  await api.streamThread(3, 41, () => {});
  assert.equal(calls[0].options.headers['Last-Event-ID'], '41');
});

test('streamThread passes the signal on, and says when the stream opened', async () => {
  const calls = respondWith(stored(1, 'turn.started', { prompt_text: 'x' }));
  const controller = new AbortController();
  let opened = 0;
  const seen = [];
  await api.streamThread(1, null, (e) => seen.push(e.type), controller.signal, () => { opened += 1; });
  assert.equal(calls[0].options.signal, controller.signal);
  assert.equal(opened, 1);
  assert.deepEqual(seen, ['turn.started']);
});

test('stored frames and typing frames both come through, and typing has no seq', async () => {
  respondWith(stored(1, 'turn.started', { prompt_text: 'hi' }) + typing('Hel') + typing('lo') + stored(2, 'assistant.message', { text: 'Hello' }));
  const events = [];
  await api.streamThread(1, null, (e) => events.push(e));
  assert.deepEqual(events.map((e) => e.type), ['turn.started', 'text.delta', 'text.delta', 'assistant.message']);
  assert.equal(events[1].seq, undefined);
  assert.equal(events[3].seq, 2);
});

test('a stream replayed through the reducer ends where the live one did', async () => {
  const frames = stored(1, 'turn.started', { prompt_text: 'hi' }) + stored(2, 'assistant.message', { text: 'Hello' }) + stored(3, 'turn.completed', {});
  respondWith(frames);
  let live = emptyTranscript();
  await api.streamThread(1, null, (e) => { live = applyEvent(live, e); });
  // Reconnect with the same events again (a server that replays from an older cursor).
  respondWith(frames);
  let again = live;
  await api.streamThread(1, 0, (e) => { again = applyEvent(again, e); });
  assert.deepEqual(again, live);
  assert.equal(live.items.length, 3);
});

test('streamThread rejects with the status when the thread is not there', async () => {
  respondJson(404, { detail: 'Thread not found' });
  await assert.rejects(api.streamThread(9, null, () => {}), (err) => err instanceof ApiError && err.status === 404);
});

test('a stream cut off mid-way rejects instead of looking finished', async () => {
  const encoder = new TextEncoder();
  let sent = false;
  globalThis.fetch = async () => new Response(new ReadableStream({
    pull(controller) {
      if (!sent) { sent = true; controller.enqueue(encoder.encode(stored(1, 'turn.started', { prompt_text: 'x' }))); return; }
      controller.error(new TypeError('network error'));
    },
  }), { status: 200 });
  const seen = [];
  await assert.rejects(api.streamThread(1, null, (e) => seen.push(e.type)));
  assert.deepEqual(seen, ['turn.started']);
});

test('an error the backend explains keeps its code and its message', async () => {
  respondJson(409, { detail: { error_code: 'turn_running', message: 'A turn is already running.' } });
  await assert.rejects(api.sendTurn(1, 'hi'), (err) => err instanceof ApiError && err.status === 409 && err.code === 'turn_running' && err.message === 'A turn is already running.');
});

test('an error without a code has a null code', async () => {
  respondJson(404, { detail: 'Not Found' });
  await assert.rejects(api.getThread(5), (err) => err instanceof ApiError && err.code === null && err.message === 'Not Found');
});

test('validation errors still read as before', async () => {
  respondJson(422, { detail: [{ loc: ['body', 'prompt_text'], msg: 'must not be blank' }] });
  await assert.rejects(api.sendTurn(1, ' '), (err) => err instanceof ApiError && /prompt_text/.test(err.message) && err.code === null);
});

test('sendTurn posts the prompt, and an approval answer goes to the right thread and id', async () => {
  const calls = respondJson(202, { id: 1, seq: 1, status: 'running' });
  await api.sendTurn(7, 'hello there');
  assert.match(calls[0].url, /\/threads\/7\/turns$/);
  assert.equal(calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].options.body), { prompt_text: 'hello there' });

  const answers = respondJson(200, { status: 'recorded', decision: 'allow' });
  await api.answerThreadApproval(7, 'a b/c', 'allow');
  assert.match(answers[0].url, /\/threads\/7\/approvals\/a%20b%2Fc$/);
  assert.deepEqual(JSON.parse(answers[0].options.body), { decision: 'allow' });
});

test('creating a thread and resetting its session use the documented routes', async () => {
  const created = respondJson(201, { id: 4 });
  await api.createThread();
  assert.match(created[0].url, /\/threads$/);
  assert.equal(created[0].options.method, 'POST');
  const reset = respondJson(200, { id: 4 });
  await api.resetThreadSession(4);
  assert.match(reset[0].url, /\/threads\/4\/session\/reset$/);
  assert.equal(reset[0].options.method, 'POST');
});

test('listing asks for archived conversations only when told to', async () => {
  const plain = respondJson(200, []);
  await api.listThreads();
  assert.match(plain[0].url, /\/threads$/);
  const all = respondJson(200, []);
  await api.listThreads(true);
  assert.match(all[0].url, /\/threads\?include_archived=true$/);
});

test('stop posts to the thread, and a stop that came too late carries its code', async () => {
  const ok = respondJson(202, { status: 'stopping', turn_id: 5 });
  assert.deepEqual(await api.stopThread(3), { status: 'stopping', turn_id: 5 });
  assert.match(ok[0].url, /\/threads\/3\/stop$/);
  assert.equal(ok[0].options.method, 'POST');

  respondJson(409, { detail: { error_code: 'no_running_turn', message: 'Nothing is running in this conversation.' } });
  await assert.rejects(api.stopThread(3), (err) => err instanceof ApiError && err.code === 'no_running_turn');
});

test('rename and archive are one PATCH with only what changed', async () => {
  const calls = respondJson(200, { id: 3, title: 'New name' });
  await api.updateThread(3, { title: 'New name' });
  assert.match(calls[0].url, /\/threads\/3$/);
  assert.equal(calls[0].options.method, 'PATCH');
  assert.deepEqual(JSON.parse(calls[0].options.body), { title: 'New name' });
  const archive = respondJson(200, { id: 3 });
  await api.updateThread(3, { archived: true });
  assert.deepEqual(JSON.parse(archive[0].options.body), { archived: true });
});

test('archiving a running conversation is refused with the code the page acts on', async () => {
  respondJson(409, { detail: { error_code: 'turn_running', message: 'Stop the running answer before archiving this conversation.' } });
  await assert.rejects(api.updateThread(3, { archived: true }), (err) => err.status === 409 && err.code === 'turn_running');
});

test('delete sends DELETE and a 204 with no body is a success', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(null, { status: 204 });
  };
  assert.equal(await api.deleteThread(3), undefined);
  assert.match(calls[0].url, /\/threads\/3$/);
  assert.equal(calls[0].options.method, 'DELETE');
});
