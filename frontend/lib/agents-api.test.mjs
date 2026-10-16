import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, ApiError } from './api.ts';
import { activeAgents, agentNames, modelFromInput, modelLabel, approvalModeLabel } from './agents.ts';

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
