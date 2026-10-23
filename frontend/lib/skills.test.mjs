import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api } from './api.ts';
import { isSkillName, parseSkillCommand, skillCompletions, skillErrorText, skillSkeleton, slugSkillName, sourceLabel, toggleSkill, usedSkills } from './skills.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
function respond(status, body) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  };
  return calls;
}

test('slugSkillName makes a valid name', () => {
  assert.equal(slugSkillName('  Review the Expenses! '), 'review-the-expenses');
  assert.equal(slugSkillName('Crème brûlée'), 'creme-brulee');
  assert.equal(slugSkillName('???'), '');
  assert.equal(slugSkillName('a'.repeat(100)).length, 64);
  assert.ok(isSkillName(slugSkillName('Weekly report 2')));
  assert.ok(!isSkillName('Bad Name') && !isSkillName('') && !isSkillName('a--b') && !isSkillName('-a'));
});

test('skeleton has the six sections and seeds the purpose', () => {
  const s = skillSkeleton('Check expenses against the policy. Flag anything over 50.');
  for (const h of ['Purpose', 'Required inputs', 'Steps', 'Validation checks', 'Output format', 'Approval boundary']) assert.match(s.body, new RegExp(`## ${h}`));
  assert.match(s.body, /Check expenses against the policy\. Flag anything over 50\./);
  assert.equal(s.description, 'Check expenses against the policy.');
  assert.equal(s.name, 'check-expenses-against-the-policy');
  const empty = skillSkeleton();
  assert.equal(empty.name, ''); assert.equal(empty.description, '');
});

test('parseSkillCommand only matches known skills', () => {
  const known = ['expense-review', 'brief'];
  assert.deepEqual(parseSkillCommand('/expense-review  q3 file.csv', known), { name: 'expense-review', rest: 'q3 file.csv' });
  assert.deepEqual(parseSkillCommand('/brief', known), { name: 'brief', rest: '' });
  assert.equal(parseSkillCommand('/unknown go', known), null);
  assert.equal(parseSkillCommand('please /brief', known), null);
  assert.equal(parseSkillCommand('/usr/bin/ls', known), null);
});

test('completions and toggling respect enabled state and the limit of three', () => {
  const list = [
    { name: 'brief', enabled: true }, { name: 'budget', enabled: true }, { name: 'bank', enabled: false }, { name: 'other', enabled: true },
  ];
  assert.deepEqual(skillCompletions('/b', list).map((s) => s.name), ['brief', 'budget']);
  assert.deepEqual(skillCompletions('/b', list, ['brief']).map((s) => s.name), ['budget']);
  assert.deepEqual(skillCompletions('/brief now', list), []);
  assert.deepEqual(skillCompletions('hello', list), []);
  assert.deepEqual(toggleSkill(['a'], 'b'), ['a', 'b']);
  assert.deepEqual(toggleSkill(['a', 'b'], 'a'), ['b']);
  assert.deepEqual(toggleSkill(['a', 'b', 'c'], 'd'), ['a', 'b', 'c']);
});

test('skills routes', async () => {
  let calls = respond(200, []);
  await api.listSkills(3);
  assert.match(calls[0].url, /\/agents\/3\/skills$/);
  calls = respond(200, {});
  await api.saveSkill(3, 'a-b', { description: 'd', body: 'b', enabled: false, source: 'user' });
  assert.equal(calls[0].options.method, 'PUT');
  assert.match(calls[0].url, /\/agents\/3\/skills\/a-b$/);
  assert.deepEqual(JSON.parse(calls[0].options.body), { description: 'd', body: 'b', enabled: false, source: 'user' });
  calls = respond(204);
  await api.deleteSkill(3, 'a-b');
  assert.equal(calls[0].options.method, 'DELETE');
  calls = respond(200, { name: 'x', description: '', body: '', warnings: [] });
  await api.previewSkill(3, '# hi');
  assert.match(calls[0].url, /\/agents\/3\/skills\/import$/);
  assert.deepEqual(JSON.parse(calls[0].options.body), { markdown: '# hi' });
});

test('sendTurn adds skills only when chosen', async () => {
  let calls = respond(200, { id: 1 });
  await api.sendTurn(1, 'hi');
  assert.deepEqual(JSON.parse(calls[0].options.body), { prompt_text: 'hi' });
  calls = respond(200, { id: 1 });
  await api.sendTurn(1, 'hi', ['f1'], ['brief']);
  assert.deepEqual(JSON.parse(calls[0].options.body), { prompt_text: 'hi', attachment_ids: ['f1'], skills: ['brief'] });
});

test('small helpers', () => {
  assert.deepEqual(usedSkills({ names: ['a', 3, 'b'] }), ['a', 'b']);
  assert.deepEqual(usedSkills({}), []);
  assert.equal(sourceLabel('imported'), 'Imported');
  assert.match(skillErrorText({ code: 'skill_unavailable', message: 'x' }), /no longer available/);
});
