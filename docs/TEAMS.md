# Teams and delegated work

A team has a lead and specialists. Existing agents keep their settings until you choose a lead or add them to the team. A team can contain an agent on any engine; team tools currently require Claude.

The database stores team membership, creation records, jobs, room membership and reply queues. Chat threads keep their existing kind after migration. Job and member work use their own conversation records; room conversation records hold visible messages.

The foundation migration uses additive columns and new tables, with an SQLite-safe downgrade. It does not start paid work or change existing agents automatically.

## Handing work out without waiting

The lead's `delegate` tool assigns work to one to four teammates and returns receipts at once. The lead then ends its turn. Each result is stored in an inbox; when every awaited result is in, or a teammate asks the person a question, the lead is resumed once in a new turn on the same conversation, with the results in a message written by the app. Results are quoted data and never approve a tool call. `ask_agent` stays for a short question the lead must have answered before it can go on (it waits up to `TEAM_JOB_WAIT_SECONDS`). A teammate that needs the person ends its reply with a last line `NEEDS INPUT: <question>`; the person answers in that teammate's own conversation and the job runs again. Pressing Stop on a lead that is waiting stops what it handed out and it is not resumed. After a restart, a job interrupted before its engine took the prompt is queued again once; otherwise it is marked failed, and a lead whose turn was cut is resumed with a note to check what it already did.

Verification: the migrated SQLite schema and PostgreSQL DDL snapshots were checked locally. A real Claude lead handing work to two specialists and being resumed once was checked locally on SQLite. Long MCP calls, real room members and PostgreSQL multiple backend execution have not been verified.
