import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api } from './api.ts';
import { compactedLabel, isTopicName, journalLabel, recalledLabel, topicFileName } from './memory.ts';
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
