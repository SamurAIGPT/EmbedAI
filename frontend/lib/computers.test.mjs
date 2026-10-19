import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api } from './api.ts';
import { canAlwaysAllow, decisionText } from './approvals.ts';
import {
  computerChip,
  computerCreationGate,
  computerStateLabel,
  computersUnsupported,
  effectiveState,
  imageLabel,
  isComputerTool,
  lastCommandText,
  limitsText,
  nextActions,
  toolLabel,
} from './computers.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function respondJson(status, body) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  };
  return calls;
}

const computer = (more = {}) => ({
  id: 1, provider: 'docker', name: 'open-dots-computer-01234567-1', state: 'running', network: 'none',
  image: 'docker.io/library/python:3.12-slim-bookworm@sha256:abc', agent_id: 2, image_pinned: true, image_id: null,
  cpus: 1, memory_mb: 1024, pids_limit: 256, idle_minutes: 30, auto_start: true, live_state: null,
  needs_recreate: false, problem: null, last_error: null, workspace_path: '/w', guest_workspace: '/workspace',
  container_id: 'abc', busy: false, last_used_at: null, last_command: null,
  health: { provider: 'docker', available: true, version: '27', error: null }, ...more,
});

test('computer calls use the computer routes', async () => {
  const calls = respondJson(200, computer());
  await api.computersHealth();
  await api.getAgentComputer(2);
  await api.createAgentComputer(2, { network: 'default', start: true });
  await api.updateComputer(1, { cpus: 2 });
  await api.computerAction(1, 'prepare-image');
  await api.deleteComputer(1);
  assert.match(calls[0].url, /\/computers\/health$/);
  assert.match(calls[1].url, /\/agents\/2\/computer$/);
  assert.equal(calls[2].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[2].options.body), { network: 'default', start: true });
  assert.equal(calls[3].options.method, 'PATCH');
  assert.match(calls[4].url, /\/computers\/1\/prepare-image$/);
  assert.equal(calls[5].options.method, 'DELETE');
});

test('an older backend without computers is told apart from an agent without one', () => {
  assert.equal(computersUnsupported(404, null), true);
  assert.equal(computersUnsupported(404, 'no_computer'), false);
  assert.equal(computersUnsupported(503, 'docker_unavailable'), false);
});

test('computer creation is gated by the selected engine capability', () => {
  assert.deepEqual(computerCreationGate(true, 'Claude'), { enabled: true, reason: null });

  const unsupported = computerCreationGate(false, 'Codex');
  assert.equal(unsupported.enabled, false);
  assert.match(unsupported.reason, /Codex can't run its tools in an agent computer/);
  assert.match(unsupported.reason, /engine that supports computers/);

  const unknown = computerCreationGate(undefined, 'Codex');
  assert.equal(unknown.enabled, false);
  assert.match(unknown.reason, /support for Codex could not be confirmed/);
});

test('labels for state, image, limits and the chip', () => {
  assert.equal(computerStateLabel('missing'), 'Not made yet');
  assert.equal(computerStateLabel(null), 'Unknown');
  assert.equal(computerStateLabel('weird'), 'weird');
  assert.equal(effectiveState(computer({ state: 'running', live_state: 'stopped' })), 'stopped');
  assert.equal(effectiveState(computer({ state: 'stopped' })), 'stopped');
  assert.equal(imageLabel('docker.io/library/python:3.12-slim-bookworm@sha256:abc'), 'python:3.12-slim-bookworm');
  assert.equal(limitsText(computer({ cpus: 1.5, memory_mb: 2048 })), '1.5 CPU · 2 GB · 256 processes');
  assert.equal(limitsText(computer({ memory_mb: 512 })), '1 CPU · 512 MB · 256 processes');
  assert.equal(computerChip(null), null);
  assert.equal(computerChip(computer()), 'Computer · running');
  assert.equal(computerChip(computer(), 'stopped'), 'Computer · stopped');
});

test('computer tools are recognised and described', () => {
  assert.ok(isComputerTool('mcp__computer__exec'));
  assert.ok(!isComputerTool('Bash'));
  assert.ok(!isComputerTool(undefined));
  assert.equal(toolLabel('mcp__computer__exec'), 'run a command in its computer');
  assert.equal(toolLabel('mcp__computer__write_file'), 'write a file in its computer');
  assert.equal(toolLabel('Write'), 'Write');
});

test('the next actions follow the state', () => {
  assert.deepEqual(nextActions(computer({ state: 'missing' })), ['prepare-image', 'start']);
  assert.deepEqual(nextActions(computer({ state: 'stopped' })), ['start', 'recreate']);
  assert.deepEqual(nextActions(computer({ state: 'running' })), ['stop', 'recreate']);
  assert.deepEqual(nextActions(computer({ live_state: 'foreign' })), []);
  assert.deepEqual(nextActions(computer({ live_state: 'unsafe' })), ['recreate']);
  assert.deepEqual(nextActions(computer({ health: { provider: 'docker', available: false, version: null, error: 'down' } })), []);
});

test('the last command reads as one line', () => {
  assert.equal(lastCommandText(computer()), null);
  const last = { tool: 'exec', subject: 'make test', exit_code: 2, timed_out: false, output: '', at: null };
  assert.equal(lastCommandText(computer({ last_command: last })), 'make test (exit 2)');
  assert.equal(lastCommandText(computer({ last_command: { ...last, tool: 'read_file', subject: 'a.txt', exit_code: 0 } })), 'read_file a.txt (exit 0)');
  assert.equal(lastCommandText(computer({ last_command: { ...last, timed_out: true } })), 'make test (timed out)');
});

test('a computer command can be always allowed, and automatic answers say why', () => {
  assert.ok(canAlwaysAllow('mcp__computer__exec', { command: 'ls' }));
  assert.ok(!canAlwaysAllow('mcp__computer__write_file', { path: 'a', content: 'b' }));
  const base = { id: '1', at: '', thread_id: 1, turn_id: 1, approval_id: 'a', tool: 'mcp__computer__read_file', kind: 'file_read', summary: 'computer: read_file a.txt', rule: null, remembered: false, mode: 'ask' };
  assert.match(decisionText({ ...base, event: 'auto_allowed', source: 'computer_read' }), /reading inside the agent's computer/);
  assert.match(decisionText({ ...base, event: 'auto_allowed', source: 'saved_rule' }), /a saved rule allowed it/);
  assert.match(decisionText({ ...base, event: 'denied', source: 'plan_mode' }), /plan only changes nothing/);
  assert.match(decisionText({ ...base, event: 'denied', source: 'user' }), /^You denied/);
});
