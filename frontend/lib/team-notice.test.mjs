import assert from 'node:assert/strict';
import { test } from 'node:test';
import { applyEvent, emptyTranscript } from './transcript.ts';

let seq = 0;
const ev = (type, data) => ({ thread_id: 1, seq: ++seq, type, turn_id: null, data, ts: '2026-01-01T00:00:00+00:00' });

test('reports, refusals and retries show as notice items; a chip with no text is dropped', () => {
  seq = 0;
  let state = applyEvent(emptyTranscript(), ev('team.notice.chip', { family: 'incident', text: 'Incident: Ada failed in its main conversation', thread_id: 7 }));
  state = applyEvent(state, ev('team.notice.chip', { family: 'escalation', text: 'Escalation: Bo is parked', thread_id: 8 }));
  state = applyEvent(state, ev('team.notice.refused', { text: 'The report could not reach the lead' }));
  state = applyEvent(state, ev('team.notice.retried', { text: 'Retried Ada', thread_id: 7 }));
  state = applyEvent(state, ev('team.notice.chip', { family: 'incident', text: '' }));
  assert.deepEqual(state.items.map((i) => [i.kind, i.tone, i.threadId]), [
    ['team_notice', 'incident', 7], ['team_notice', 'escalation', 8], ['team_notice', 'refused', null], ['team_notice', 'retried', 7],
  ]);
  assert.equal(state.lastSeq, seq);
});
