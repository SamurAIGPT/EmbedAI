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
