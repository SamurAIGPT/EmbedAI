"""Durable team work and shared conversations."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, Integer, String, Text, Identity, ForeignKey, Index, false, func, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime, JSONValue

class Team(Base):
    __tablename__ = "team"
    __table_args__ = (
        Index("uq_team_owner", 'owner_id', unique=True),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    lead_agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())


class TeamEvent(Base):
    __tablename__ = "team_event"
    __table_args__ = (
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    data: Mapped[dict] = mapped_column(JSONValue, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())


class AgentJob(Base):
    __tablename__ = "agent_job"
    __table_args__ = (
        Index("ix_agent_job_requester_turn", 'requester_turn_id', unique=False),
        Index("ix_agent_job_status", 'owner_id', 'status', unique=False),
        Index("ix_agent_job_parent", 'parent_id', unique=False),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    root_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    origin_kind: Mapped[str] = mapped_column(String, nullable=False, default='person', server_default='person')
    origin_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    origin_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    requester_agent_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requester_thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requester_turn_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assignee_agent_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assignee_thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    assignee_turn_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mode: Mapped[str] = mapped_column(String, nullable=False, default='work', server_default='work')
    brief: Mapped[str] = mapped_column(Text, nullable=False)
    brief_hash: Mapped[str] = mapped_column(String, nullable=False)
    done_when: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default='queued', server_default='queued')
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_mode: Mapped[str] = mapped_column(String, nullable=False, default='ask', server_default='ask')
    executions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    budget_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=24, server_default='24')
    budget_executions: Mapped[int] = mapped_column(Integer, nullable=False, default=48, server_default='48')
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Non-blocking delegation. `kind` is 'root' for the row a turn's first
    # hand-off creates, else 'job'. `reply` is 'wait' (the requester polls, the
    # blocking ask), 'resume' (the requester is resumed with the result) or
    # 'none'. `current_turn_id` is the turn running this row's own execution.
    kind: Mapped[str] = mapped_column(String, nullable=False, default='job', server_default='job')
    reply: Mapped[str] = mapped_column(String, nullable=False, default='wait', server_default='wait')
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    restart: Mapped[str | None] = mapped_column(String, nullable=True)
    restarts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    current_turn_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rework: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    budget_wakes: Mapped[int] = mapped_column(Integer, nullable=False, default=48, server_default='48')


class AgentJobEvent(Base):
    __tablename__ = "agent_job_event"
    __table_args__ = (
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agent_job.id", name="fk_agent_job_event_job_id_agent_job"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONValue, nullable=False, default=dict)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())


class Room(Base):
    __tablename__ = "room"
    __table_args__ = (
        Index("uq_room_owner_name_live", 'owner_id', 'name', unique=True, sqlite_where=text("archived_at IS NULL"), postgresql_where=text("archived_at IS NULL")),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    bulletin: Mapped[str] = mapped_column(Text, nullable=False, default='', server_default='')
    default_responder_kind: Mapped[str] = mapped_column(String, nullable=False, default='member', server_default='member')
    default_responder_agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_read_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default='0')
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    deleting_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False, default='person', server_default='person')
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())


class RoomMember(Base):
    __tablename__ = "room_member"
    __table_args__ = (
        Index("uq_room_member", 'room_id', 'agent_id', unique=True),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    room_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("room.id", name="fk_" + __tablename__ + "_room_id_room"), nullable=False)
    agent_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    member_thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_seen_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default='0')
    held_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())


class RoomDispatch(Base):
    __tablename__ = "room_dispatch"
    __table_args__ = (
        Index("uq_room_dispatch_message_agent", 'room_id', 'message_seq', 'agent_id', unique=True),
        Index("ix_room_dispatch_queue", 'room_id', 'status', unique=False),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    room_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("room.id", name="fk_" + __tablename__ + "_room_id_room"), nullable=False)
    message_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False, default='default', server_default='default')
    chain_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    status: Mapped[str] = mapped_column(String, nullable=False, default='queued', server_default='queued')
    turn_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)




class TeamSetupRequest(Base):
    """One applied or decided change to the team's agents (a setup plan or a
    deletion). The row is the receipt: a repeated request id replays its result,
    and undo reads what it needs from `undo`."""

    __tablename__ = "team_setup_request"
    __table_args__ = (
        Index("uq_team_setup_request_owner_request", 'owner_id', 'request_id', unique=True),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lead_agent_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    turn_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default='setup', server_default='setup')
    plan: Mapped[dict] = mapped_column(JSONValue, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String, nullable=False, default='applied', server_default='applied')
    result: Mapped[dict] = mapped_column(JSONValue, nullable=False, default=dict)
    undo: Mapped[dict] = mapped_column(JSONValue, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
