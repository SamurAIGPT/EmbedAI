# Teams and delegated work

A team has a lead and specialists. Existing agents keep their settings until you choose a lead or add them to the team. A team can contain an agent on any engine; team tools currently require Claude.

The database stores team membership, creation records, jobs, room membership and reply queues. Chat threads keep their existing kind after migration. Job and member work use their own conversation records; room conversation records hold visible messages.

The foundation migration uses additive columns and new tables, with an SQLite-safe downgrade. It does not start paid work or change existing agents automatically.

## Handing work out without waiting

The lead's `delegate` tool assigns work to one to four teammates and returns receipts at once. The lead then ends its turn. Each result is stored in an inbox; when every awaited result is in, or a teammate asks the person a question, the lead is resumed once in a new turn on the same conversation, with the results in a message written by the app. Results are quoted data and never approve a tool call. `ask_agent` stays for a short question the lead must have answered before it can go on (it waits up to `TEAM_JOB_WAIT_SECONDS`). A teammate that needs the person ends its reply with a last line `NEEDS INPUT: <question>`; the person answers in that teammate's own conversation and the job runs again. Pressing Stop on a lead that is waiting stops what it handed out and it is not resumed. After a restart, a job interrupted before its engine took the prompt is queued again once; otherwise it is marked failed, and a lead whose turn was cut is resumed with a note to check what it already did.

Verification: the migrated SQLite schema and PostgreSQL DDL snapshots were checked locally. A real Claude lead handing work to two specialists and being resumed once was checked locally on SQLite. Long MCP calls, real room members and PostgreSQL multiple backend execution have not been verified.

## When a teammate's run goes wrong

A teammate's chat that fails, stalls (nothing happened for `TURN_STALL_SECONDS`), cannot start, or is cut off by a crash is reported to the lead once the turn has settled. The report is a harness message in the lead's "Team incidents" conversation (listed with your chats): the request, the teammate's last words and what failed, quoted as data inside JSON, redacted, one line each. A Stop is never reported, nor is a shutdown. A failure of a delegated job is reported to the lead that is waiting for it as the job's result, with outcome `stalled` for a stall, not as an incident.

The lead can call `retry_thread` to continue that conversation. It refuses with a code when the caller is not the lead, the teammate or thread is not on the team, the thread is a room or job conversation, it is running, it finished a run since it was reported, it was already retried twice within the hour, or its session is broken. The teammate keeps its own permission mode, and is told the lead asked. Each thread has at most five reports an hour: the third says retries are used up, after the fifth they are muted and you are told once.

If the lead is busy, reports wait and are folded into one turn when it is free; none is refused. If there is no lead, the lead itself failed, or it cannot take the report, you get a notification instead (not twice for something you already heard about, such as a failed routine).

**Escalations** (setting `TEAM_ESCALATIONS`, off by default): a teammate can call `raise_escalation` to flag a gate only you can lift, and an approval left unanswered for ten minutes is flagged the same way. The lead is told in "Team escalations" and assigns nothing: it tells you what is asked and where to answer. Three flags an hour per thread, then muted.

Not verified: a real Claude lead taking an incident and retrying; computers for notice turns (a lead whose computer is unavailable fails the report turn and you are notified).
