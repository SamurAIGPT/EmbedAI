import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, ApiError } from './api.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function rejectWith(body, status = 422, statusText = 'Unprocessable Entity') {
  globalThis.fetch = async () => new Response(JSON.stringify(body), {
    status, statusText, headers: { 'Content-Type': 'application/json' },
  });
}

test('shows FastAPI field-validation messages rather than object coercion', async () => {
  rejectWith({ detail: [
    { loc: ['body', 'prompt_text'], msg: 'Field required', type: 'missing' },
    { loc: ['body', 'fields', 0], msg: 'Invalid field', type: 'value_error' },
  ] });
  await assert.rejects(api.submitTask('user', 'box', ''), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 422);
    assert.equal(error.message, 'body.prompt_text: Field required; body.fields.0: Invalid field');
    return true;
  });
});

test('preserves existing string details', async () => {
  rejectWith({ detail: 'Task not found' }, 404, 'Not Found');
  await assert.rejects(api.getTask(1), { message: 'Task not found' });
});

test('falls back for empty or unusable validation details', async () => {
  for (const detail of [[], [null, {}], null]) {
    rejectWith({ detail });
    await assert.rejects(api.getTask(1), { message: 'Unprocessable Entity' });
  }
});

test('answerLocalApproval posts the decision to that one approval and nothing else', async () => {
  let request = null;
  globalThis.fetch = async (url, init) => {
    request = { url: String(url), init };
    return new Response(JSON.stringify({ status: 'recorded', decision: 'deny' }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };
  await api.answerLocalApproval(7, 'abc/../def', 'deny');
  assert.ok(request.url.endsWith('/local/tasks/7/approvals/abc%2F..%2Fdef'));
  assert.equal(request.init.method, 'POST');
  assert.deepEqual(JSON.parse(request.init.body), { decision: 'deny' });
});

test('a question that is no longer waiting surfaces as a 404 ApiError', async () => {
  rejectWith({ detail: 'That request is no longer waiting for an answer' }, 404, 'Not Found');
  await assert.rejects(api.answerLocalApproval(1, 'x', 'allow'), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 404);
    return true;
  });
});

test('listLocalApprovals reads the pending questions for one run', async () => {
  let requested = '';
  globalThis.fetch = async (url) => {
    requested = String(url);
    return new Response(JSON.stringify([{ id: 'a', task_id: 3, tool: 'Bash', summary: 'ls', input: { command: 'ls' }, created_at: 'now' }]), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };
  const approvals = await api.listLocalApprovals(3);
  assert.ok(requested.endsWith('/local/tasks/3/approvals'));
  assert.equal(approvals[0].tool, 'Bash');
});

test('getProviders reads /providers and returns the boat status', async () => {
  let requested = '';
  globalThis.fetch = async (url) => {
    requested = String(url);
    return new Response(JSON.stringify({ boat: { enabled: false, reason: 'BOAT_API_KEY is not set in backend/.env' } }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };
  const providers = await api.getProviders();
  assert.ok(requested.endsWith('/providers'));
  assert.equal(providers.boat.enabled, false);
  assert.match(providers.boat.reason, /BOAT_API_KEY/);
});
