# Teams and delegated work

A team has a lead and specialists. Existing agents keep their settings until you choose a lead or add them to the team. A team can contain an agent on any engine; team tools currently require Claude.

The database stores team membership, creation records, jobs, room membership and reply queues. Chat threads keep their existing kind after migration. Job and member work use their own conversation records; room conversation records hold visible messages.

The foundation migration uses additive columns and new tables, with an SQLite-safe downgrade. It does not start paid work or change existing agents automatically.

Verification: the migrated SQLite schema and PostgreSQL DDL snapshots were checked locally. Real Claude delegation, long MCP calls, real room members and PostgreSQL multiple backend execution have not been verified.
