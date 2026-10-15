import assert from 'node:assert/strict';
import { test } from 'node:test';
import { applyEvent, applyTyping, emptyTranscript, pendingApprovals } from './transcript.ts';

let seq = 0;
const ev = (type, data = {}, turn_id = 1) => ({ thread_id: 1, seq: ++seq, type, turn_id, data, ts: '2026-01-01T00:00:00+00:00' });
const typing = (text, mode = 'append') => ({ thread_id: 1, type: 'text.delta', turn_id: 1, data: { text, mode } });
const run = (events, start = emptyTranscript()) => events.reduce(applyEvent, start);
const kinds = (t) => t.items.map((i) => i.kind);

function reset() {
  seq = 0;
}

test('a plain turn reads in order and ends with nothing running', () => {
  reset();
  const t = run([
    ev('turn.started', { prompt_text: 'hi' }),
    ev('assistant.message', { text: 'hello' }),
    ev('turn.completed', { status: 'succeeded', result: 'hello' }),
  ]);
  assert.deepEqual(kinds(t), ['user', 'assistant', 'turn_end']);
  assert.equal(t.items[0].text, 'hi');
  assert.equal(t.items[1].text, 'hello');
  assert.equal(t.items[2].status, 'completed');
  assert.equal(t.runningTurnId, null);
  assert.equal(t.lastSeq, 3);
});

test('a turn is running from turn.started until its end event', () => {
  reset();
  let t = run([ev('turn.started', { prompt_text: 'x' }, 7)]);
  assert.equal(t.runningTurnId, 7);
  t = run([ev('assistant.message', { text: 'y' }, 7)], t);
  assert.equal(t.runningTurnId, 7);
  t = run([ev('turn.completed', {}, 7)], t);
  assert.equal(t.runningTurnId, null);
});

test('an end event for another turn does not clear the running one', () => {
  reset();
  const t = run([ev('turn.started', { prompt_text: 'x' }, 8), ev('turn.interrupted', { reason: 'shutdown' }, 7)]);
  assert.equal(t.runningTurnId, 8);
});

test('applying the same events again changes nothing (a replay after a reconnect)', () => {
  reset();
  const events = [
    ev('turn.started', { prompt_text: 'hi' }),
    ev('assistant.message', { text: 'one' }),
    ev('tool.started', { call_id: 'c1', name: 'Bash', summary: 'ls', input: { command: 'ls' } }),
    ev('tool.completed', { call_id: 'c1', ok: true, output: 'a', truncated: false }),
  ];
  const once = run(events);
  const twice = run(events, once);
  assert.deepEqual(twice, once);
  assert.equal(twice.items.length, 4 - 1); // user, assistant, one tool step
});

test('an event older than the cursor is ignored', () => {
  reset();
  const t = run([ev('turn.started', { prompt_text: 'a' }), ev('assistant.message', { text: 'b' })]);
  const late = { thread_id: 1, seq: 1, type: 'assistant.message', turn_id: 1, data: { text: 'old' } };
  assert.equal(applyEvent(t, late), t);
});

test('a tool call is one step: started, then completed by call_id', () => {
  reset();
  let t = run([ev('turn.started', { prompt_text: 'x' }), ev('tool.started', { call_id: 'c1', name: 'Bash', summary: 'ls -la', input: { command: 'ls -la' } })]);
  const step = t.items[1];
  assert.equal(step.kind, 'tool');
  assert.equal(step.state, 'running');
  assert.deepEqual(step.input, { command: 'ls -la' });
  t = run([ev('tool.completed', { call_id: 'c1', ok: true, output: 'total 0', truncated: false })], t);
  assert.equal(t.items.length, 2);
  assert.equal(t.items[1].state, 'done');
  assert.equal(t.items[1].output, 'total 0');
});

test('a failed or cut-off tool result is marked', () => {
  reset();
  const t = run([
    ev('tool.started', { call_id: 'a', name: 'Bash', summary: '', input: {} }),
    ev('tool.started', { call_id: 'b', name: 'Read', summary: '', input: {} }),
    ev('tool.completed', { call_id: 'a', ok: false, output: 'boom', truncated: false }),
    ev('tool.completed', { call_id: 'b', ok: true, output: 'xxxx', truncated: true }),
  ]);
  assert.equal(t.items[0].state, 'error');
  assert.equal(t.items[1].state, 'done');
  assert.equal(t.items[1].truncated, true);
});

test('two calls with different ids do not mix, and ids are matched exactly', () => {
  reset();
  const t = run([
    ev('tool.started', { call_id: 'a', name: 'Bash', summary: '1', input: {} }),
    ev('tool.started', { call_id: 'b', name: 'Bash', summary: '2', input: {} }),
    ev('tool.completed', { call_id: 'b', ok: true, output: 'second', truncated: false }),
  ]);
  assert.equal(t.items[0].state, 'running');
  assert.equal(t.items[1].state, 'done');
  assert.equal(t.items[1].output, 'second');
});

test('a completion whose start was never seen is still shown', () => {
  reset();
  const t = run([ev('tool.completed', { call_id: 'zz', ok: true, output: 'late', truncated: false })]);
  assert.equal(t.items.length, 1);
  assert.equal(t.items[0].kind, 'tool');
  assert.equal(t.items[0].output, 'late');
});

test('typing appends, a snapshot replaces, and the stored message clears it', () => {
  reset();
  let t = run([ev('turn.started', { prompt_text: 'x' })]);
  t = applyTyping(t, typing('Hel'));
  t = applyTyping(t, typing('lo'));
  assert.equal(t.typing, 'Hello');
  t = applyTyping(t, typing('Hello wor', 'replace'));
  assert.equal(t.typing, 'Hello wor');
  t = applyEvent(t, ev('assistant.message', { text: 'Hello world' }));
  assert.equal(t.typing, null);
  assert.equal(t.items.at(-1).text, 'Hello world');
});

test('typing frames go through applyEvent too and never move the cursor', () => {
  reset();
  let t = run([ev('turn.started', { prompt_text: 'x' })]);
  const before = t.lastSeq;
  t = applyEvent(t, typing('abc'));
  assert.equal(t.typing, 'abc');
  assert.equal(t.lastSeq, before);
});

test('the end of a turn clears any typing preview', () => {
  reset();
  let t = run([ev('turn.started', { prompt_text: 'x' })]);
  t = applyTyping(t, typing('half a sent'));
  t = applyEvent(t, ev('turn.failed', { status: 'failed', error: 'x', error_code: 'timeout' }));
  assert.equal(t.typing, null);
});

test('an approval waits, then is answered', () => {
  reset();
  let t = run([
    ev('turn.started', { prompt_text: 'write it' }),
    ev('approval.requested', { approval_id: 'ap1', tool: 'Write', kind: 'file_write', summary: 'a.txt', input: { file_path: 'a.txt', content: 'hi' } }),
  ]);
  assert.equal(pendingApprovals(t).length, 1);
  assert.deepEqual(pendingApprovals(t)[0].input, { file_path: 'a.txt', content: 'hi' });
  assert.equal(pendingApprovals(t)[0].approvalKind, 'file_write');
  t = applyEvent(t, ev('approval.resolved', { approval_id: 'ap1', status: 'allow' }));
  assert.equal(pendingApprovals(t).length, 0);
  assert.equal(t.items[1].status, 'allowed');
});

test('deny, expiry and cancellation are told apart', () => {
  reset();
  const t = run([
    ev('approval.requested', { approval_id: 'a', tool: 'Bash', kind: 'command', summary: '', input: {} }),
    ev('approval.requested', { approval_id: 'b', tool: 'Bash', kind: 'command', summary: '', input: {} }),
    ev('approval.requested', { approval_id: 'c', tool: 'Bash', kind: 'command', summary: '', input: {} }),
    ev('approval.resolved', { approval_id: 'a', status: 'deny' }),
    ev('approval.resolved', { approval_id: 'b', status: 'expired' }),
    ev('approval.resolved', { approval_id: 'c', status: 'cancelled' }),
  ]);
  assert.deepEqual(t.items.map((i) => i.status), ['denied', 'expired', 'cancelled']);
});

test('an approval that was resolved stays resolved if a stale event repeats it', () => {
  reset();
  let t = run([
    ev('approval.requested', { approval_id: 'a', tool: 'Bash', kind: 'command', summary: '', input: {} }),
    ev('approval.resolved', { approval_id: 'a', status: 'allow' }),
  ]);
  t = applyEvent(t, ev('approval.resolved', { approval_id: 'a', status: 'deny' }));
  assert.equal(t.items[0].status, 'allowed');
});

test('a turn that ends with an approval open closes it: nobody can answer it any more', () => {
  reset();
  const t = run([
    ev('turn.started', { prompt_text: 'x' }, 3),
    ev('approval.requested', { approval_id: 'a', tool: 'Bash', kind: 'command', summary: '', input: {} }, 3),
    ev('turn.interrupted', { reason: 'shutdown' }, 3),
  ]);
  assert.equal(pendingApprovals(t).length, 0);
  assert.equal(t.items[1].status, 'cancelled');
});

test('a turn ending does not touch another turn\'s open approval', () => {
  reset();
  const t = run([
    ev('approval.requested', { approval_id: 'a', tool: 'Bash', kind: 'command', summary: '', input: {} }, 5),
    ev('turn.completed', {}, 4),
  ]);
  assert.equal(pendingApprovals(t).length, 1);
});

test('a failed turn keeps its partial message, marked incomplete, and shows why it failed', () => {
  reset();
  const t = run([
    ev('turn.started', { prompt_text: 'x' }),
    ev('assistant.message', { text: 'It was going to say', incomplete: true }),
    ev('turn.failed', { status: 'failed', error: 'The CLI stopped', error_code: 'cli_exit', result: 'It was going to say' }),
  ]);
  assert.equal(t.items[1].incomplete, true);
  const end = t.items[2];
  assert.equal(end.status, 'failed');
  assert.equal(end.error, 'The CLI stopped');
  assert.equal(end.errorCode, 'cli_exit');
});

test('an interrupted turn says why and is not a failure', () => {
  reset();
  const t = run([ev('turn.started', { prompt_text: 'x' }), ev('turn.interrupted', { reason: 'lease_expired' })]);
  const end = t.items.at(-1);
  assert.equal(end.status, 'interrupted');
  assert.equal(end.reason, 'lease_expired');
});

test('a session reset is a notice in the transcript, and earlier items stay', () => {
  reset();
  const t = run([
    ev('turn.started', { prompt_text: 'x' }),
    ev('turn.failed', { status: 'failed', error_code: 'session_resume_failed' }),
    ev('session.reset', { message: 'Started a new session', previous_state: 'broken' }, null),
  ]);
  assert.deepEqual(kinds(t), ['user', 'turn_end', 'session_reset']);
  assert.equal(t.items[2].message, 'Started a new session');
});

test('an unknown event type moves the cursor and draws nothing', () => {
  reset();
  const t = run([ev('turn.started', { prompt_text: 'x' }), ev('something.new', { a: 1 })]);
  assert.equal(t.items.length, 1);
  assert.equal(t.lastSeq, 2);
});

test('runtime.status and other unnumbered frames are not part of the transcript', () => {
  reset();
  const t = run([ev('turn.started', { prompt_text: 'x' })]);
  const same = applyEvent(t, { thread_id: 1, type: 'runtime.status', data: { state: 'ok' } });
  assert.equal(same, t);
});

test('missing or odd fields do not throw', () => {
  reset();
  const t = run([
    { thread_id: 1, seq: 1, type: 'turn.started', data: {} },
    { thread_id: 1, seq: 2, type: 'assistant.message', data: { text: 42 } },
    { thread_id: 1, seq: 3, type: 'tool.started', data: { input: 'plain string' } },
    { thread_id: 1, seq: 4, type: 'approval.requested', data: { input: 'not an object' } },
  ]);
  assert.equal(t.items.length, 4);
  assert.equal(t.items[0].text, '');
  assert.equal(t.items[1].text, '');
  assert.equal(t.items[2].name, 'tool');
  assert.deepEqual(t.items[3].input, {});
});

test('applying an event never changes the state it was given', () => {
  reset();
  const t = run([ev('turn.started', { prompt_text: 'x' }), ev('approval.requested', { approval_id: 'a', tool: 'Bash', kind: 'command', summary: '', input: {} })]);
  const snapshot = JSON.stringify(t);
  applyEvent(t, ev('approval.resolved', { approval_id: 'a', status: 'allow' }));
  applyEvent(t, ev('assistant.message', { text: 'y' }));
  applyTyping(t, typing('z'));
  assert.equal(JSON.stringify(t), snapshot);
});

test('a stopped turn closes its unfinished tool steps and keeps the finished ones', () => {
  reset();
  const t = run([
    ev('turn.started', { prompt_text: 'x' }, 4),
    ev('tool.started', { call_id: 'a', name: 'Bash', summary: '', input: {} }, 4),
    ev('tool.completed', { call_id: 'a', ok: true, output: 'fine', truncated: false }, 4),
    ev('tool.started', { call_id: 'b', name: 'Bash', summary: '', input: {} }, 4),
    ev('turn.cancelled', { status: 'cancelled', error_code: 'stopped' }, 4),
  ]);
  assert.equal(t.items[1].state, 'done');
  assert.equal(t.items[2].state, 'stopped');
  assert.equal(t.items.at(-1).status, 'cancelled');
  assert.equal(t.runningTurnId, null);
});

test('an interrupted or failed turn also leaves no tool step spinning', () => {
  for (const type of ['turn.interrupted', 'turn.failed']) {
    reset();
    const t = run([
      ev('turn.started', { prompt_text: 'x' }, 6),
      ev('tool.started', { call_id: 'a', name: 'Bash', summary: '', input: {} }, 6),
      ev(type, {}, 6),
    ]);
    assert.equal(t.items[1].state, 'stopped', type);
  }
});

test('a turn ending does not touch another turn\'s running tool step', () => {
  reset();
  const t = run([
    ev('tool.started', { call_id: 'a', name: 'Bash', summary: '', input: {} }, 9),
    ev('turn.cancelled', {}, 8),
  ]);
  assert.equal(t.items[0].state, 'running');
});

test('a stopped answer keeps its partial text, marked unfinished, in order', () => {
  reset();
  const t = run([
    ev('turn.started', { prompt_text: 'write a lot' }, 2),
    ev('assistant.message', { text: 'It began', incomplete: true }, 2),
    ev('turn.cancelled', { status: 'cancelled', error_code: 'stopped', result: 'It began' }, 2),
  ]);
  assert.deepEqual(kinds(t), ['user', 'assistant', 'turn_end']);
  assert.equal(t.items[1].incomplete, true);
  assert.equal(t.items[2].status, 'cancelled');
});
