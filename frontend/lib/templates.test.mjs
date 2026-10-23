import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, ApiError } from './api.ts';
import { categoryLabel, filterTemplates, installTemplateSkills, skillInstallNotice, uniqueAgentName } from './templates.ts';

const t = (id, name, role, category, description = '') => ({ id, name, role, category, description, instructions: '', color: '#000000', example_requests: [], requires: [], available: true, starter_notes: null, skills: [] });
const list = [
  t('everyday-helper', 'Helper', 'General helper', 'everyday', 'Answers questions'),
  t('work-builder', 'Builder', 'Coding partner', 'work', 'Makes small tested changes'),
  t('writing-plainer', 'Plainer', 'Editor', 'writing', 'Natural tone'),
];

test('filtering is case-insensitive over name, role, description and category', () => {
  assert.deepEqual(filterTemplates(list, 'BUILDER', null).map((x) => x.id), ['work-builder']);
  assert.deepEqual(filterTemplates(list, 'coding partner', null).map((x) => x.id), ['work-builder']);
  assert.deepEqual(filterTemplates(list, 'tested', null).map((x) => x.id), ['work-builder']);
  assert.deepEqual(filterTemplates(list, 'writing', null).map((x) => x.id), ['writing-plainer']);
  assert.equal(filterTemplates(list, 'nothing here', null).length, 0);
});

test('category and search combine; empty search keeps everything', () => {
  assert.equal(filterTemplates(list, '', null).length, 3);
  assert.equal(filterTemplates(list, '  ', 'work').length, 1);
  assert.equal(filterTemplates(list, 'helper', 'work').length, 0);
});

test('category labels', () => {
  assert.equal(categoryLabel('sales'), 'Sales');
  assert.equal(categoryLabel('misc'), 'Misc');
});

test('repeated Add gets a unique name within 80 characters', () => {
  assert.equal(uniqueAgentName('Helper', []), 'Helper');
  assert.equal(uniqueAgentName('Helper', ['helper']), 'Helper 2');
  assert.equal(uniqueAgentName('Helper', ['Helper', 'Helper 2', 'helper 3']), 'Helper 4');
  const long = 'x'.repeat(80);
  const next = uniqueAgentName(long, [long]);
  assert.equal(next.length, 80);
  assert.ok(next.endsWith(' 2'));
});

const skills = [{ name: 'a-skill', description: 'd', body: 'b' }, { name: 'b-skill', description: 'd', body: 'b' }];

test('skills install one by one and report success', async () => {
  const calls = [];
  const result = await installTemplateSkills(7, 'work-numbers', skills, async (id, tpl, skill) => { calls.push([id, tpl, skill.name]); });
  assert.deepEqual(result, { installed: ['a-skill', 'b-skill'], failed: [], unsupported: false });
  assert.deepEqual(calls, [[7, 'work-numbers', 'a-skill'], [7, 'work-numbers', 'b-skill']]);
  assert.equal(skillInstallNotice(result), null);
});

test('a 404 from an older backend is a non-fatal notice', async () => {
  const result = await installTemplateSkills(7, 'x', skills, async () => { throw new ApiError(404, 'Not Found'); });
  assert.equal(result.unsupported, true);
  assert.deepEqual(result.failed, ['a-skill', 'b-skill']);
  assert.match(skillInstallNotice(result), /no skills library/);
});

test('another failure skips that skill and keeps going', async () => {
  const result = await installTemplateSkills(7, 'x', skills, async (_i, _t, skill) => { if (skill.name === 'a-skill') throw new ApiError(422, 'bad'); });
  assert.deepEqual(result, { installed: ['b-skill'], failed: ['a-skill'], unsupported: false });
  assert.match(skillInstallNotice(result), /a-skill/);
});

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('the skill is saved with its template as the source', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url: String(url), options }); return new Response('{}', { status: 200 }); };
  await api.installTemplateSkill(3, 'work-numbers', skills[0]);
  assert.match(calls[0].url, /\/agents\/3\/skills\/a-skill$/);
  assert.equal(calls[0].options.method, 'PUT');
  assert.deepEqual(JSON.parse(calls[0].options.body), { description: 'd', body: 'b', enabled: true, source: 'template:work-numbers' });
});
