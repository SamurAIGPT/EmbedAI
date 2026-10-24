# Tasks and calendar

Open **Tasks & calendar** from the chat sidebar to plan work. The board,
calendar, and upcoming list show the same saved tasks. Tasks belong to the
instance owner and remain in the database when the app restarts.

## Plan a task

Create a task with a title and optional description, priority, deadline,
agent or team assignment, and linked conversation. The default board has
Backlog, Ready, In progress, Waiting, Review, and Done columns. Move a card
by dragging it to a column or changing its status in the task editor.
Moving into Done records completion; moving out reopens it.

Assignment records who the work is for. It does not start a conversation or
send a prompt. Removing an assigned agent or linked conversation preserves
the task; edit it to clear or replace the unavailable link.

Archive tasks to remove them from the active plan. Archived tasks remain
available for restoration. Search and filters help find work without changing
its saved status or deadline.

## Plan dates

Use the month or week calendar to see deadlines. Creating a task from a day
prefills its deadline. Moving a task to another day changes its deadline.
The task editor provides the same operation without dragging.

Date-only deadlines stay on their chosen calendar date and become overdue
after that day ends. Timed deadlines identify a specific instant and become
overdue when that time passes. Times display in the browser's local timezone;
the timezone is shown in the planner and task editor. Moving a timed deadline
to a different day retains its local clock time. A time skipped by a daylight
saving change must be replaced with a valid time in the editor.

Completed and archived tasks are not marked overdue. The upcoming list also
helps review overdue work and tasks without deadlines.

## Routines on the calendar

The optional routine overlay shows future schedule occurrences and recorded
routine runs, distinct from task deadlines. Future dates are calculated by
the existing routine scheduler; they are projections rather than a guarantee
that a run will succeed. Paused routines have no future projections. Recorded
runs can still appear in their original date range.

Open **Routines** to create or edit an automation, pause it, run it now, or
inspect its history. A calendar run with a linked conversation can open that
conversation. Routine entries cannot be rescheduled by dragging a task.
Large date ranges or frequent schedules can produce more occurrences than
the planner displays; the calendar reports when results are limited.

## Saving and concurrent edits

Tasks are saved through the backend, with a revision on every edit. If
another window changes a task before you save, the old edit is rejected.
Load the latest version, review it, and apply your change again.

## Scope

This release provides one default board. It does not connect to Google or
Outlook calendars, import meetings, or synchronize provider events. It also
does not start or schedule agent work from task cards or send deadline
notifications. Use the existing Routines workflow for scheduled agent runs.

A deadline or status change never starts an agent. Existing routine approval,
retry, and run-limit behavior continues to apply to scheduled work.
