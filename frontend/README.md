# Open Duet — Web UI

Next.js app for Open Duet: chat with named agents, manage their instructions,
memory, routines, approvals and computers, and sign in or pair a device. It also
holds the original page for connecting Telegram and Claude and submitting Boat
tasks, backed by the same API.

For prerequisites and how to run the whole stack (this app, the backend API,
and the Telegram bot), see [docs/SETUP.md](../docs/SETUP.md). This file covers
frontend-specific details; scheduling's backend behavior is covered in the
[root README's scheduling overview](../README.md#scheduling-tasks).

## Layout

- `app/page.tsx` — the four-step wizard (get started → connect Claude →
  connect GitHub → submit a task).
- `components/` — one component per step, plus shared `Card`/`Button` bits.
  `SubmitTask.tsx` covers both one-off tasks and, via its "Repeat this on a
  schedule" checkbox, recurring ones — the frequency dropdown (hourly/daily/
  weekdays/weekly) and time picker build a cron expression client-side
  (`buildCronExpression`) using the browser's own timezone
  (`Intl.DateTimeFormat().resolvedOptions().timeZone`), then post to
  `/schedules` instead of `/tasks`. The schedule list below it supports
  pause/resume/delete.
- `lib/api.ts` — typed client for the backend's `/api/v1` routes, including
  `Schedule` and the `*Schedule` calls.

## Running just the frontend

```bash
cp .env.example .env.local   # set NEXT_PUBLIC_API_BASE_URL if not using defaults
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The backend API must
already be running (see `../backend/README.md`) and its `FRONTEND_ORIGINS`
must include this app's origin, or requests will fail CORS.
