import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api } from './api.ts';
import {
  badgeText,
  curlExample,
  canSaveSchedulePreview,
  emptySchedule,
  isActiveRun,
  localInputValue,
  nextRunText,
  runStateLabel,
  runStateTone,
  scheduleDraftOf,
  schedulePreviewKey,
  scheduleSpec,
  triggerLabel,
  webhookUrl,
} from './routines.ts';

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

test('a draft becomes the spec of its kind only', () => {
  const draft = { ...emptySchedule('Europe/Berlin'), days: [5, 1], time: '07:30' };
  assert.deepEqual(scheduleSpec(draft), { days: [1, 5], time: '07:30' });
  assert.deepEqual(scheduleSpec({ ...draft, kind: 'interval', everyMinutes: '90' }), { every_minutes: 90 });
  assert.deepEqual(scheduleSpec({ ...draft, kind: 'cron', expression: ' */5 * * * * ' }), { expression: '*/5 * * * *' });
  assert.deepEqual(scheduleSpec({ ...draft, kind: 'once', at: '2026-10-18T09:00' }), { at: '2026-10-18T09:00' });
  assert.deepEqual(scheduleSpec({ ...draft, kind: 'manual' }), {});
});

test('schedule save requires a valid preview for the current draft', () => {
  const weekdays = { ...emptySchedule('UTC'), days: [1, 3, 5] };
  const weekdayPreview = schedulePreviewKey(weekdays);
  assert.equal(canSaveSchedulePreview(weekdays, null), false);
  assert.equal(canSaveSchedulePreview(weekdays, weekdayPreview), true);

  const noWeekdays = { ...weekdays, days: [] };
  assert.equal(canSaveSchedulePreview(noWeekdays, weekdayPreview), false);
  assert.equal(canSaveSchedulePreview(noWeekdays, schedulePreviewKey(noWeekdays)), false);

  const interval = { ...emptySchedule('UTC'), kind: 'interval', everyMinutes: '60' };
  const intervalPreview = schedulePreviewKey(interval);
  assert.equal(canSaveSchedulePreview({ ...interval, everyMinutes: '30' }, intervalPreview), false);
});

test('a saved routine opens as the same draft', () => {
  const draft = scheduleDraftOf({ schedule_kind: 'weekdays', schedule_spec: { days: [0, 6], time: '18:00' }, timezone: 'UTC' });
  assert.deepEqual([draft.kind, draft.days, draft.time, draft.timezone], ['weekdays', [0, 6], '18:00', 'UTC']);
  const anchor = '2026-10-18T09:45:00+00:00';
  const interval = scheduleDraftOf({ schedule_kind: 'interval', schedule_spec: { every_minutes: 60, anchor }, timezone: 'UTC' });
  assert.equal(interval.intervalAnchor, anchor);
  assert.deepEqual(scheduleSpec(interval), { every_minutes: 60, anchor });
  assert.deepEqual(scheduleSpec({ ...interval, everyMinutes: '120' }), { every_minutes: 120 });
  const once = scheduleDraftOf({ schedule_kind: 'once', schedule_spec: { at: '2026-10-18T03:30:00+00:00' }, timezone: 'Asia/Kolkata' });
  assert.equal(once.at, '2026-10-18T09:00');
  assert.equal(localInputValue('not a date', 'UTC'), '');
});

test('an unchanged one-time minute preserves its original seconds and milliseconds', () => {
  const originalAt = '2026-10-18T03:30:42.987+00:00';
  const draft = scheduleDraftOf({ schedule_kind: 'once', schedule_spec: { at: originalAt }, timezone: 'Asia/Kolkata' });
  assert.equal(draft.at, '2026-10-18T09:00');
  assert.deepEqual(scheduleSpec(draft), { at: originalAt });

  assert.deepEqual(scheduleSpec({ ...draft, at: '2026-10-18T09:01' }), { at: '2026-10-18T09:01' });
  assert.deepEqual(scheduleSpec({ ...draft, at: '2026-10-18T09:00' }), { at: originalAt });
});

test('run states and triggers read as words', () => {
  assert.equal(runStateLabel('needs_input'), 'Needs your approval');
  assert.equal(runStateTone('needs_input'), 'warn');
  assert.equal(runStateTone('interrupted'), 'bad');
  assert.equal(runStateTone('skipped'), 'muted');
  assert.equal(triggerLabel('webhook'), 'Webhook');
  assert.equal(isActiveRun({ state: 'queued' }), true);
  assert.equal(isActiveRun({ state: 'completed' }), false);
  assert.equal(isActiveRun(null), false);
});

test('next run text covers paused, manual and none', () => {
  assert.equal(nextRunText({ paused: true, next_run_at: null, schedule_kind: 'cron', timezone: 'UTC' }), 'Paused');
  assert.match(nextRunText({ paused: false, next_run_at: null, schedule_kind: 'manual', timezone: 'UTC' }), /by hand/);
  assert.equal(nextRunText({ paused: false, next_run_at: null, schedule_kind: 'once', timezone: 'UTC' }), 'No upcoming run');
  assert.match(nextRunText({ paused: false, next_run_at: '2026-10-19T07:00:00Z', schedule_kind: 'cron', timezone: 'UTC' }), /^Next: /);
});

test('webhook url, curl and badge', () => {
  assert.equal(webhookUrl('/hooks/4', 'http://127.0.0.1:8000/api/v1'), 'http://127.0.0.1:8000/hooks/4');
  assert.match(curlExample('http://x/hooks/4', 'rt_abc'), /Authorization: Bearer rt_abc/);
  assert.equal(badgeText(0), null);
  assert.equal(badgeText(7), '7');
  assert.equal(badgeText(120), '99+');
});

test('routine calls use the routine routes', async () => {
  const calls = respondJson(200, []);
  await api.listRoutines();
  await api.previewSchedule('cron', { expression: '* * * * *' }, 'UTC');
  await api.runRoutineNow(3);
  await api.enableRoutineWebhook(3);
  await api.unreadNotifications();
  await api.readAllNotifications();
  assert.deepEqual(calls.map((c) => [c.options?.method ?? 'GET', new URL(c.url).pathname]), [
    ['GET', '/api/v1/routines'],
    ['POST', '/api/v1/routines/preview'],
    ['POST', '/api/v1/routines/3/run'],
    ['POST', '/api/v1/routines/3/webhook'],
    ['GET', '/api/v1/notifications/unread-count'],
    ['POST', '/api/v1/notifications/read-all'],
  ]);
  assert.deepEqual(JSON.parse(calls[1].options.body), { schedule_kind: 'cron', schedule_spec: { expression: '* * * * *' }, timezone: 'UTC' });
});
