import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, ApiError, decisionQuery } from './api.ts';
import {
  canAlwaysAllow,
  csvFileName,
  decisionEventLabel,
  decisionText,
  issueKindLabel,
  ruleCommand,
  ruleMatchLabel,
  ruleScopeLabel,
} from './approvals.ts';
import { applyEvent, emptyTranscript } from './transcript.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function respond(status, body, type = 'application/json') {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    const text = typeof body === 'string' ? body : JSON.stringify(body);
    return new Response(status === 204 ? null : text, { status, headers: { 'Content-Type': type } });
  };
  return calls;
}

const entry = (event, more = {}) => ({ id: '1', at: '2026-01-01T00:00:00Z', event, summary: 'ls -la', tool: 'Bash', ...more });

test('decision text says what happened and who decided', () => {
  assert.equal(decisionText(entry('allowed')), 'You allowed ls -la');
  assert.match(decisionText(entry('allowed', { remembered: true })), /always allow/);
  assert.equal(decisionText(entry('denied')), 'You denied ls -la');
  assert.match(decisionText(entry('auto_allowed')), /saved rule allowed it/);
  assert.match(decisionText(entry('shown', { rule: 'safety_floor:delete-home' })), /safety floor/);
  assert.match(decisionText(entry('expired')), /did not run/);
  assert.equal(decisionText(entry('undone', { rule: 'exact@/w: npm test', summary: null })), 'You stopped always allowing npm test');
  assert.equal(decisionText(entry('shown', { summary: null, tool: null })), 'Asked about a tool call');
});

test('event labels fall back to the raw name', () => {
  assert.equal(decisionEventLabel('auto_allowed'), 'Allowed by a saved rule');
  assert.equal(decisionEventLabel('something_new'), 'something_new');
});

test('rule words', () => {
  assert.equal(ruleCommand('prefix@/a b: git status'), 'git status');
  assert.equal(ruleCommand('plain'), 'plain');
  assert.equal(ruleCommand(null), '');
  assert.equal(ruleMatchLabel({ match: 'prefix' }), 'Starts with');
  assert.equal(ruleMatchLabel({ match: 'exact' }), 'Exactly');
  assert.equal(ruleScopeLabel({ workspace: '/w' }), 'In /w');
  assert.equal(ruleScopeLabel({ workspace: null }), 'In every folder');
});

test('only a shell command with text can be always allowed', () => {
  assert.equal(canAlwaysAllow('Bash', { command: 'ls' }), true);
  assert.equal(canAlwaysAllow('Bash', { command: '  ' }), false);
  assert.equal(canAlwaysAllow('Bash', {}), false);
  assert.equal(canAlwaysAllow('Write', { command: 'ls' }), false);
});

test('csv file names and issue labels', () => {
  const day = new Date('2026-10-17T10:00:00Z');
  assert.equal(csvFileName(3, day), 'decisions-thread-3-2026-10-17.csv');
  assert.equal(csvFileName(null, day), 'decisions-2026-10-17.csv');
  assert.equal(issueKindLabel('retry_loop'), 'Retry loop');
  assert.equal(issueKindLabel('other'), 'other');
});

test('decision queries leave out what is not set', () => {
  assert.equal(decisionQuery({}), '');
  assert.equal(decisionQuery({ limit: 50, before: 'x y', event: 'denied', thread_id: 0 }), '?limit=50&before=x+y&event=denied&thread_id=0');
  assert.equal(decisionQuery({ event: null, thread_id: null, before: null }), '');
});

test('remember is sent only when asked for, and the answer names the rule', async () => {
  let calls = respond(200, { status: 'recorded', decision: 'allow', rule_id: 7, remember_error: null });
  const answer = await api.answerThreadApproval(1, 'a b', 'allow', true);
  assert.equal(answer.rule_id, 7);
  assert.match(calls[0].url, /\/threads\/1\/approvals\/a%20b$/);
  assert.deepEqual(JSON.parse(calls[0].options.body), { decision: 'allow', remember: true });
  calls = respond(200, { status: 'recorded', decision: 'deny' });
  await api.answerThreadApproval(1, 'a', 'deny');
  assert.deepEqual(JSON.parse(calls[0].options.body), { decision: 'deny' });
});

test('rules: list per folder, add, and delete with the conversation', async () => {
  let calls = respond(200, []);
  await api.listApprovalRules('/work/my repo');
  assert.match(calls[0].url, /\/approval-rules\?workspace=%2Fwork%2Fmy%20repo$/);
  calls = respond(200, []);
  await api.listApprovalRules(null);
  assert.match(calls[0].url, /\/approval-rules$/);
  calls = respond(201, { id: 1, match: 'prefix', command: 'git status', workspace: null, created_at: 'x' });
  await api.addApprovalRule({ match: 'prefix', command: 'git status' });
  assert.equal(calls[0].options.method, 'POST');
  calls = respond(204, '');
  await api.deleteApprovalRule(4, 9);
  assert.match(calls[0].url, /\/approval-rules\/4\?thread_id=9$/);
  assert.equal(calls[0].options.method, 'DELETE');
  calls = respond(204, '');
  await api.deleteApprovalRule(4);
  assert.match(calls[0].url, /\/approval-rules\/4$/);
});

test('a bad rule is an ApiError with the reason', async () => {
  respond(422, { detail: 'this command is on the safety floor and is always asked about' });
  await assert.rejects(api.addApprovalRule({ match: 'exact', command: 'rm -rf ~' }), (err) =>
    err instanceof ApiError && err.status === 422 && /safety floor/.test(err.message));
});

test('decisions page and csv export use the filters', async () => {
  let calls = respond(200, { items: [], next_before: null });
  await api.listDecisions({ limit: 50, event: 'undone', thread_id: 2, before: 'c' });
  assert.match(calls[0].url, /\/decisions\?limit=50&before=c&event=undone&thread_id=2$/);
  calls = respond(200, 'at,event\n', 'text/csv');
  assert.equal(await api.exportDecisionsCsv({ event: 'denied', before: 'c', limit: 5 }), 'at,event\n');
  assert.match(calls[0].url, /\/decisions\/export\.csv\?event=denied$/);
  respond(403, { detail: { error_code: 'launch_token_required', message: 'no' } });
  await assert.rejects(api.exportDecisionsCsv(), (err) => err instanceof ApiError && err.code === 'launch_token_required');
});

test('health scan asks for the window', async () => {
  const calls = respond(200, { since: 'x', scanned_events: 0, scanned_decisions: 0, issues: [] });
  await api.scanHealth(168);
  assert.match(calls[0].url, /\/sentinel\/scan\?hours=168$/);
});

function stored(seq, type, data) {
  return { thread_id: 1, seq, type, turn_id: 1, data, ts: 'x' };
}

test('an approval a saved rule answered says so', () => {
  let t = emptyTranscript();
  t = applyEvent(t, stored(1, 'approval.requested', { approval_id: 'a', tool: 'Bash', input: { command: 'ls' } }));
  assert.equal(t.items[0].resolvedBy, '');
  assert.equal(t.items[0].ruleId, null);
  t = applyEvent(t, stored(2, 'approval.resolved', { approval_id: 'a', status: 'allowed', by: 'saved_rule', rule_id: 3 }));
  assert.equal(t.items[0].status, 'allowed');
  assert.equal(t.items[0].resolvedBy, 'saved_rule');
  assert.equal(t.items[0].ruleId, 3);
});

test('a person\'s answer has no resolver', () => {
  let t = emptyTranscript();
  t = applyEvent(t, stored(1, 'approval.requested', { approval_id: 'a', tool: 'Bash', input: {} }));
  t = applyEvent(t, stored(2, 'approval.resolved', { approval_id: 'a', status: 'denied' }));
  assert.equal(t.items[0].resolvedBy, '');
  assert.equal(t.items[0].ruleId, null);
});
