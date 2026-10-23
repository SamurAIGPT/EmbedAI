import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api } from './api.ts';
import { captureLine, compactedLabel, helperModelLine, isTopicName, journalLabel, recalledLabel, tidyHourLabel, topicFileName, withHelperModel } from './memory.ts';
import { applyEvent, emptyTranscript } from './transcript.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('topic names are single safe file names', () => {
  assert.equal(isTopicName('travel.md'), true);
  for (const bad of ['MEMORY.md', '../x.md', '.x.md', 'a.b.md', 'x.txt', '']) assert.equal(isTopicName(bad), false, bad);
  assert.equal(topicFileName("Asha's School"), 'asha-s-school.md');
  assert.equal(topicFileName('travel.md'), 'travel.md');
  assert.equal(topicFileName('!!!'), null);
});

test('labels say what was recalled and what was folded', () => {
  assert.equal(recalledLabel(1, 2), 'Recalled 1 note and 2 passages from earlier conversations');
  assert.equal(recalledLabel(0, 1), 'Recalled 1 passage from earlier conversations');
  assert.match(compactedLabel(12), /^Earlier messages summarized: 12 messages were folded/);
  assert.equal(journalLabel({ actor: 'person', kind: 'edited', path: 'memory/a.md', added: 2, removed: 1 }), 'You edited memory/a.md (+2 −1)');
  assert.equal(journalLabel({ actor: 'undo', kind: 'deleted', path: 'memory/a.md', added: 0, removed: 3 }), 'Undo deleted memory/a.md');
  assert.equal(journalLabel({ actor: 'upkeep', kind: 'created', path: 'memory/a.md', added: 1, removed: 0 }), 'Memory upkeep created memory/a.md');
});

test('the transcript shows a Recalled chip and a summarized marker', () => {
  let t = emptyTranscript();
  t = applyEvent(t, { seq: 1, type: 'turn.started', turn_id: 4, data: { prompt_text: 'hi there friend' } });
  t = applyEvent(t, {
    seq: 2, type: 'memory.recalled', turn_id: 4,
    data: { notes: [{ name: 'car.md', title: null, snippet: 'service in March' }], conversations: [{ thread_id: 3, title: 'Car', role: 'user', at: '2026-10-01T00:00:00', snippet: 'blue Skoda' }] },
  });
  t = applyEvent(t, { seq: 3, type: 'context.compacted', turn_id: 4, data: { folded_messages: 6, summary: 'S' } });
  assert.deepEqual(t.items.map((i) => i.kind), ['user', 'recalled', 'compacted']);
  const recalled = t.items[1];
  assert.equal(recalled.notes[0].name, 'car.md');
  assert.equal(recalled.conversations[0].snippet, 'blue Skoda');
  assert.equal(t.items[2].folded, 6);
  // A malformed event still draws a chip, with nothing in it rather than a crash.
  t = applyEvent(t, { seq: 4, type: 'memory.recalled', turn_id: 5, data: { notes: 'x' } });
  assert.deepEqual(t.items[3].notes, []);
});

test('topic, journal and proposal calls hit the right routes', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), method: options?.method ?? 'GET', body: options?.body });
    return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  await api.saveTopic(3, 'travel.md', 'x', 'h');
  await api.undoMemoryChange(3, 'abc');
  await api.acceptProposal(3, 'p1');
  await api.memoryProposals(3);
  assert.match(calls[0].url, /\/agents\/3\/memory\/topics\/travel\.md$/);
  assert.equal(calls[0].method, 'PUT');
  assert.deepEqual(JSON.parse(calls[0].body), { text: 'x', expected_hash: 'h' });
  assert.match(calls[1].url, /\/agents\/3\/memory\/journal\/abc\/undo$/);
  assert.match(calls[2].url, /\/agents\/3\/memory\/proposals\/p1\/accept$/);
  assert.match(calls[3].url, /\/agents\/3\/memory\/proposals$/);
});

test('upkeep labels say what ran, when, and on which model', () => {
  assert.equal(tidyHourLabel(0), '12:00 am');
  assert.equal(tidyHourLabel(3), '3:00 am');
  assert.equal(tidyHourLabel(12), '12:00 pm');
  assert.equal(tidyHourLabel(23), '11:00 pm');
  assert.equal(tidyHourLabel(24), 'never');
  assert.equal(captureLine(null), '');
  assert.equal(captureLine({ at: 1, added: 0, topics: 0, note: null }), '');
  assert.equal(captureLine({ at: 1, added: 1, topics: 2, about_me: 1, note: null }), 'Last noticed: 1 for the notes, 2 for topic notes, 1 for About me.');
  assert.equal(captureLine({ at: 1, added: 0, topics: 0, note: 'The capture call did not answer.' }), 'The capture call did not answer.');
  assert.equal(helperModelLine(null), '');
  assert.equal(helperModelLine({ engine: 'claude', helper_model: 'haiku', engine_can_call: true }), 'Quick calls use haiku on claude.');
  assert.match(helperModelLine({ engine: 'openai_compat', helper_model: null, engine_can_call: true }), /own model on openai_compat/);
  assert.match(helperModelLine({ engine: 'codex', helper_model: null, engine_can_call: false }), /cannot make the quick call/);
  assert.equal(journalLabel({ actor: 'upkeep', via: 'tidy', kind: 'edited', path: 'MEMORY.md', added: 0, removed: 1 }), 'Memory upkeep (tidy-up) edited MEMORY.md (+0 −1)');
  assert.deepEqual(withHelperModel({ claude: 'haiku' }, 'openai_compat', ' llama3 '), { claude: 'haiku', openai_compat: 'llama3' });
  assert.deepEqual(withHelperModel({ claude: 'haiku' }, 'claude', '  '), {});
});

test('upkeep, settings and About me calls hit the right routes', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), method: options?.method ?? 'GET', body: options?.body });
    return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  await api.memoryUpkeep(3);
  await api.tidyAgentMemory(3);
  await api.memorySettings();
  await api.saveMemorySettings({ learn_from_rooms: true, helper_models: { claude: 'haiku' } });
  await api.aboutMe();
  await api.saveAboutMe('I live in Pune', 'h1');
  await api.saveAboutMe('fresh', null);
  await api.removeLearnedFact('f 1');
  assert.deepEqual(calls.map((c) => [c.method, c.url.replace(/^.*\/api\/v1/, '')]), [
    ['GET', '/agents/3/memory/upkeep'],
    ['POST', '/agents/3/memory/tidy'],
    ['GET', '/memory/settings'],
    ['PUT', '/memory/settings'],
    ['GET', '/memory/about-me'],
    ['PUT', '/memory/about-me'],
    ['PUT', '/memory/about-me'],
    ['DELETE', '/memory/about-me/learned/f%201'],
  ]);
  assert.deepEqual(JSON.parse(calls[3].body), { learn_from_rooms: true, helper_models: { claude: 'haiku' } });
  assert.deepEqual(JSON.parse(calls[5].body), { text: 'I live in Pune', expected_hash: 'h1' });
  assert.deepEqual(JSON.parse(calls[6].body), { text: 'fresh' });
});
