import assert from 'node:assert/strict';
import { test } from 'node:test';
import { addDays, calendarDays, dateKey, deadlineDay, isOverdue, localDateTime, rescheduleDeadline } from './planner.ts';

test('month and week grids start Monday and include adjacent dates', () => {
  const month = calendarDays(new Date(2026, 9, 24), 'month');
  assert.equal(month.length, 42);
  assert.equal(dateKey(month[0]), '2026-09-28');
  assert.equal(dateKey(month.at(-1)), '2026-11-08');
  const week = calendarDays(new Date(2026, 9, 25), 'week');
  assert.equal(week.length, 7);
  assert.equal(dateKey(week[0]), '2026-10-19');
  assert.equal(dateKey(week.at(-1)), '2026-10-25');
});

test('date-only deadlines are due through the whole local day and completed cards are never overdue', () => {
  const item = { due_date: '2026-10-24', due_at: null, completed_at: null, archived: false };
  assert.equal(isOverdue(item, new Date(2026, 9, 24, 23, 59)), false);
  assert.equal(isOverdue(item, new Date(2026, 9, 25, 0, 0)), true);
  assert.equal(isOverdue({ ...item, completed_at: '2026-10-24T12:00:00Z' }, new Date(2026, 9, 25)), false);
  assert.equal(isOverdue({ ...item, archived: true }, new Date(2026, 9, 25)), false);
  assert.equal(isOverdue({ ...item, due_date: null }, new Date(2026, 9, 25)), false);
});

test('timed deadlines compare instants rather than calendar strings', () => {
  const item = { due_date: null, due_at: '2026-10-24T09:30:00+05:30', completed_at: null, archived: false };
  assert.equal(isOverdue(item, new Date('2026-10-24T03:59:00Z')), false);
  assert.equal(isOverdue(item, new Date('2026-10-24T04:01:00Z')), true);
});

test('date-only calendar movement never converts a day to a UTC midnight', () => {
  assert.deepEqual(rescheduleDeadline({ due_date: '2026-10-24', due_at: null }, '2026-10-25', 'Asia/Kolkata'), { due_date: '2026-10-25', due_at: null });
  assert.equal(deadlineDay({ due_date: '2026-10-24', due_at: null }), '2026-10-24');
  assert.equal(deadlineDay({ due_date: null, due_at: null }), null);
});

test('calendar dates and rescheduling retain wall time across daylight saving transitions', () => {
  const original = process.env.TZ;
  process.env.TZ = 'America/New_York';
  try {
    const before = new Date('2026-03-07T09:30:00-05:00');
    const after = addDays(before, 1);
    assert.equal(after.getHours(), 9);
    assert.equal(after.getTime() - before.getTime(), 23 * 60 * 60 * 1000);
    assert.equal(localDateTime(after.toISOString()), '2026-03-08T09:30');
    const moved = rescheduleDeadline({ due_date: null, due_at: before.toISOString() }, '2026-03-08', 'America/New_York');
    assert.equal(moved.due_at, '2026-03-08T13:30:00.000Z');
    assert.equal(moved.timezone, 'America/New_York');
    assert.equal(deadlineDay({ due_date: null, due_at: '2026-03-08T02:00:00Z' }), '2026-03-07');
    assert.throws(() => rescheduleDeadline({ due_date: null, due_at: '2026-03-07T07:30:00Z' }, '2026-03-08', 'America/New_York'), /does not exist/);
  } finally {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  }
});
