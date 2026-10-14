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
