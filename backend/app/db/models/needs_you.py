"""What the "Needs you" card remembers: the person's time zone, when they were
last here, and which card was written today. Ids, times and hashes only."""
from datetime import datetime

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONValue, UTCDateTime


class NeedsYouState(Base):
    __tablename__ = "needs_you_state"

    owner_id: Mapped[str] = mapped_column(String, primary_key=True)
    # IANA name from the person's browser; null until they have been here.
    time_zone: Mapped[str | None] = mapped_column(String, nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # One card per local day: its day, the conversation it sits in, a hash of
    # what it last said, and how many times it has been rewritten.
    card_day: Mapped[str | None] = mapped_column(String, nullable=True)
    card_thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    card_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    card_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Keys of the items already announced (the last 200).
    notified: Mapped[list] = mapped_column(JSONValue, nullable=False, default=list)
