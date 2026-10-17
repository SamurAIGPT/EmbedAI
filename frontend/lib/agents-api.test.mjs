import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, ApiError } from './api.ts';
import { activeAgents, agentFileHasNotes, agentFileText, agentNames, memoryGauge, modelFromInput, modelLabel, approvalModeLabel, parseAgentFile, redactionNotice } from './agents.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function respondJson(status, body) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  };
  return calls;
}

test('createThread sends the agent id only when there is one', async () => {
  let calls = respondJson(200, { id: 1 });
  await api.createThread(7);
  assert.deepEqual(JSON.parse(calls[0].options.body), { agent_id: 7 });
  calls = respondJson(200, { id: 2 });
  await api.createThread();
  assert.deepEqual(JSON.parse(calls[0].options.body), {});
  calls = respondJson(200, { id: 3 });
  await api.createThread(null);
  assert.deepEqual(JSON.parse(calls[0].options.body), {});
});

test('agent calls use the /agents routes', async () => {
  let calls = respondJson(200, []);
  await api.listAgents();
  assert.match(calls[0].url, /\/agents$/);
  await api.listAgents(true);
  assert.match(calls[1].url, /\/agents\?include_archived=true$/);
  calls = respondJson(200, { id: 4 });
  await api.createAgent({ name: 'Reviewer', approval_mode: 'plan' });
  assert.equal(calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].options.body), { name: 'Reviewer', approval_mode: 'plan' });
  await api.updateAgent(4, { archived: true });
  assert.match(calls[1].url, /\/agents\/4$/);
  assert.equal(calls[1].options.method, 'PATCH');
  calls = respondJson(204);
  await api.deleteAgent(4);
  assert.equal(calls[0].options.method, 'DELETE');
});

test('an older backend answers 404 as an ApiError the page can recognise', async () => {
  respondJson(404, { detail: 'Not Found' });
  await assert.rejects(api.listAgents(), (err) => err instanceof ApiError && err.status === 404);
});

test('model text: default and empty mean no model', () => {
  assert.equal(modelFromInput(' Default '), null);
  assert.equal(modelFromInput(''), null);
  assert.equal(modelFromInput(' opus '), 'opus');
  assert.equal(modelLabel(null), 'default');
  assert.equal(modelLabel('sonnet'), 'sonnet');
});

test('agent helpers', () => {
  const agents = [
    { id: 1, name: 'A', archived_at: null },
    { id: 2, name: 'B', archived_at: '2026-01-01T00:00:00Z' },
  ];
  assert.deepEqual(activeAgents(agents).map((a) => a.id), [1]);
  assert.equal(agentNames(agents).get(2), 'B');
  assert.equal(agentNames(null).size, 0);
  assert.equal(approvalModeLabel('accept_edits'), 'Accept edits');
  assert.equal(approvalModeLabel('weird'), 'weird');
});

test('notes, duplicate, export and import use their routes', async () => {
  let calls = respondJson(200, { id: 1 });
  await api.duplicateAgent(4);
  assert.match(calls[0].url, /\/agents\/4\/duplicate$/);
  assert.equal(calls[0].options.method, 'POST');

  calls = respondJson(200, { text: '', hash: 'h' });
  await api.getAgentMemory(4);
  assert.match(calls[0].url, /\/agents\/4\/memory$/);
  await api.saveAgentMemory(4, '- a', 'abc');
  assert.equal(calls[1].options.method, 'PUT');
  assert.deepEqual(JSON.parse(calls[1].options.body), { text: '- a', expected_hash: 'abc' });
  await api.saveAgentMemory(4, '- b', null);
  assert.deepEqual(JSON.parse(calls[2].options.body), { text: '- b' });

  calls = respondJson(200, { file_name: 'a.agent.json', document: {}, redacted: [] });
  await api.exportAgent(4);
  assert.match(calls[0].url, /\/agents\/4\/export$/);
  await api.exportAgent(4, true);
  assert.match(calls[1].url, /\/agents\/4\/export\?include_notes=true$/);

  calls = respondJson(201, { id: 9 });
  await api.importAgent({ format: 'x' }, false);
  assert.match(calls[0].url, /\/agents\/import$/);
  assert.deepEqual(JSON.parse(calls[0].options.body), { document: { format: 'x' }, include_notes: false });
});

test('a notes conflict surfaces as memory_changed', async () => {
  respondJson(409, { detail: { error_code: 'memory_changed', message: 'The notes changed since you opened them.' } });
  await assert.rejects(api.saveAgentMemory(1, 'x', 'h'), (err) => err instanceof ApiError && err.code === 'memory_changed');
});

test('the notes gauge goes amber from 80% and red once anything is cut', () => {
  const base = { max_lines: 200, max_bytes: 24000, truncated: false };
  assert.equal(memoryGauge({ ...base, lines: 10, bytes: 200, loaded_lines: 10 }).level, 'ok');
  assert.equal(memoryGauge({ ...base, lines: 170, bytes: 200, loaded_lines: 170 }).level, 'near');
  assert.equal(memoryGauge({ ...base, lines: 20, bytes: 20000, loaded_lines: 20 }).level, 'near');
  const over = memoryGauge({ ...base, lines: 250, bytes: 3000, loaded_lines: 200, truncated: true });
  assert.equal(over.level, 'over');
  assert.equal(over.percent, 100);
  assert.match(over.text, /250 of 200 lines/);
  assert.match(over.text, /50 lines past the limit/);
});

test('agent files are read with a sentence when they are not one', () => {
  assert.throws(() => parseAgentFile('not json'), /not JSON/);
  assert.throws(() => parseAgentFile('[1]'), /not an agent file/);
  const parsed = parseAgentFile(agentFileText({ format: 'open-dots.agent', notes: { 'MEMORY.md': '- a' } }));
  assert.equal(agentFileHasNotes(parsed), true);
  assert.equal(agentFileHasNotes({ notes: { 'MEMORY.md': '  ' } }), false);
  assert.equal(agentFileHasNotes({}), false);
});

test('the export notice names the redacted parts', () => {
  assert.equal(redactionNotice([]), null);
  assert.match(redactionNotice(['instructions', 'notes']), /instructions, notes/);
});
