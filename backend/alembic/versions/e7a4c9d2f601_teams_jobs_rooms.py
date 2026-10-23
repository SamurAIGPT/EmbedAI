"""Teams, bounded jobs and durable rooms.
Revision ID: e7a4c9d2f601
Revises: c8d2e5a9f314
"""
from alembic import context, op
import sqlalchemy as sa
from app.db.types import BigIntPK, UTCDateTime, JSONValue
revision = "e7a4c9d2f601"
down_revision = "c8d2e5a9f314"
branch_labels = None
depends_on = None

ADDITIONS = {
    "agent": [sa.Column("team_id", sa.BigInteger(), nullable=True), sa.Column("delegation_enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_by_agent_id", sa.BigInteger(), nullable=True), sa.Column("instructions_reviewed_at", UTCDateTime(), nullable=True)],
    "thread": [sa.Column("kind", sa.String(), nullable=False, server_default="chat"), sa.Column("job_id", sa.BigInteger(), nullable=True), sa.Column("room_id", sa.BigInteger(), nullable=True)],
    "approval": [sa.Column("consumed_at", UTCDateTime(), nullable=True)],
}

def upgrade():
    offline = context.is_offline_mode()
    for table, columns in ADDITIONS.items():
        present = set() if offline else {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
        for column in columns:
            if column.name not in present:
                op.add_column(table, column)
    if offline or not sa.inspect(op.get_bind()).has_table("team"):
        op.create_table("team",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("lead_agent_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
    if offline or "uq_team_owner" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("team")}:
        op.create_index("uq_team_owner", "team", ['owner_id'], unique=True)
    if offline or not sa.inspect(op.get_bind()).has_table("team_event"):
        op.create_table("team_event",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("team_id", sa.BigInteger(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), nullable=True),
            sa.Column("job_id", sa.BigInteger(), nullable=True),
            sa.Column("thread_id", sa.BigInteger(), nullable=True),
            sa.Column("data", JSONValue, nullable=False),
            sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
    if offline or not sa.inspect(op.get_bind()).has_table("agent_job"):
        op.create_table("agent_job",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("root_id", sa.BigInteger(), nullable=True),
            sa.Column("parent_id", sa.BigInteger(), nullable=True),
            sa.Column("depth", sa.Integer(), nullable=False, server_default='0'),
            sa.Column("origin_kind", sa.String(), nullable=False, server_default='person'),
            sa.Column("origin_ref", sa.String(), nullable=True),
            sa.Column("origin_quote", sa.Text(), nullable=True),
            sa.Column("requester_agent_id", sa.BigInteger(), nullable=False),
            sa.Column("requester_thread_id", sa.BigInteger(), nullable=False),
            sa.Column("requester_turn_id", sa.BigInteger(), nullable=False),
            sa.Column("assignee_agent_id", sa.BigInteger(), nullable=False),
            sa.Column("assignee_thread_id", sa.BigInteger(), nullable=True),
            sa.Column("assignee_turn_id", sa.BigInteger(), nullable=True),
            sa.Column("mode", sa.String(), nullable=False, server_default='work'),
            sa.Column("brief", sa.Text(), nullable=False),
            sa.Column("brief_hash", sa.String(), nullable=False),
            sa.Column("done_when", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default='queued'),
            sa.Column("outcome", sa.String(), nullable=True),
            sa.Column("outcome_detail", sa.Text(), nullable=True),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("approval_mode", sa.String(), nullable=False, server_default='ask'),
            sa.Column("executions", sa.Integer(), nullable=False, server_default='0'),
            sa.Column("budget_requests", sa.Integer(), nullable=False, server_default='24'),
            sa.Column("budget_executions", sa.Integer(), nullable=False, server_default='48'),
            sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", UTCDateTime(), nullable=True),
            sa.Column("finished_at", UTCDateTime(), nullable=True),
            sa.Column("last_activity_at", UTCDateTime(), nullable=True),
            sqlite_autoincrement=True,
        )
    if offline or "ix_agent_job_requester_turn" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("agent_job")}:
        op.create_index("ix_agent_job_requester_turn", "agent_job", ['requester_turn_id'], unique=False)
    if offline or "ix_agent_job_status" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("agent_job")}:
        op.create_index("ix_agent_job_status", "agent_job", ['owner_id', 'status'], unique=False)
    if offline or not sa.inspect(op.get_bind()).has_table("agent_job_event"):
        op.create_table("agent_job_event",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("agent_job.id", name="fk_agent_job_event_job_id_agent_job"), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("detail", JSONValue, nullable=False),
            sa.Column("at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
    if offline or not sa.inspect(op.get_bind()).has_table("room"):
        op.create_table("room",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("bulletin", sa.Text(), nullable=False, server_default=''),
            sa.Column("default_responder_kind", sa.String(), nullable=False, server_default='member'),
            sa.Column("default_responder_agent_id", sa.BigInteger(), nullable=True),
            sa.Column("thread_id", sa.BigInteger(), nullable=False),
            sa.Column("last_read_seq", sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column("archived_at", UTCDateTime(), nullable=True),
            sa.Column("deleting_at", UTCDateTime(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=False, server_default='person'),
            sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
    if offline or "uq_room_owner_name_live" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("room")}:
        op.create_index("uq_room_owner_name_live", "room", ['owner_id', 'name'], unique=True, sqlite_where=sa.text("archived_at IS NULL"), postgresql_where=sa.text("archived_at IS NULL"))
    if offline or not sa.inspect(op.get_bind()).has_table("room_member"):
        op.create_table("room_member",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("room_id", sa.BigInteger(), sa.ForeignKey("room.id", name="fk_room_member_room_id_room"), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), nullable=False),
            sa.Column("member_thread_id", sa.BigInteger(), nullable=True),
            sa.Column("last_seen_seq", sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column("held_at", UTCDateTime(), nullable=True),
            sa.Column("added_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
    if offline or "uq_room_member" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("room_member")}:
        op.create_index("uq_room_member", "room_member", ['room_id', 'agent_id'], unique=True)
    if offline or not sa.inspect(op.get_bind()).has_table("room_dispatch"):
        op.create_table("room_dispatch",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("room_id", sa.BigInteger(), sa.ForeignKey("room.id", name="fk_room_dispatch_room_id_room"), nullable=False),
            sa.Column("message_seq", sa.BigInteger(), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), nullable=False),
            sa.Column("reason", sa.String(), nullable=False, server_default='default'),
            sa.Column("chain_depth", sa.Integer(), nullable=False, server_default='0'),
            sa.Column("status", sa.String(), nullable=False, server_default='queued'),
            sa.Column("turn_id", sa.BigInteger(), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", UTCDateTime(), nullable=True),
            sa.Column("finished_at", UTCDateTime(), nullable=True),
            sqlite_autoincrement=True,
        )
    if offline or "uq_room_dispatch_message_agent" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("room_dispatch")}:
        op.create_index("uq_room_dispatch_message_agent", "room_dispatch", ['room_id', 'message_seq', 'agent_id'], unique=True)
    if offline or "ix_room_dispatch_queue" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("room_dispatch")}:
        op.create_index("ix_room_dispatch_queue", "room_dispatch", ['room_id', 'status'], unique=False)

def downgrade():
    op.drop_table("room_dispatch")
    op.drop_table("room_member")
    op.drop_table("room")
    op.drop_table("agent_job_event")
    op.drop_table("agent_job")
    op.drop_table("team_event")
    op.drop_table("team")
    for table, columns in reversed(list(ADDITIONS.items())):
        for column in reversed(columns):
            op.drop_column(table, column.name)
