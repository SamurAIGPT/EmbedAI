"""One routine scheduler per database (docs/RELIABILITY.md).

On SQLite the backend's file lease (app/core/data_lease.py) already means one
backend per database, so the scheduler always runs there.

PostgreSQL allows several backends on one server. Each would run the routine
scheduler, and the startup recovery of a second backend would mark the first
one's routine runs interrupted. So the scheduler runs only in the backend that
holds a session-level advisory lock, taken on a connection kept open for the
life of the process:

- PostgreSQL releases the lock when that session ends, however it ends: a clean
  shutdown unlocks it, a crash or `kill -9` closes the connection and the server
  drops it. Nothing is left to clean up by hand.
- A backend that finds the lock taken is on standby: it serves the API and runs
  turns, but starts no routine, and tries again on every scheduler pass, so it
  takes over within one pass after the holder stops.
- A holder whose lock connection breaks (the database restarted) stops
  scheduling at once and takes the lock again when it can.
"""

from dataclasses import dataclass, field

from sqlalchemy import text

from app.core.logging import get_logger

logger = get_logger(__name__)

# One fixed key for "the routine scheduler of this database" (any 64-bit value
# no other program on the same database uses: the bytes of "odroutin", which
# fit a signed bigint).
SCHEDULER_LOCK_KEY = 0x6F64_726F_7574_696E


@dataclass
class SchedulerLock:
    """Whether this process may run the routine scheduler. `engine` is an
    AsyncEngine (the app's by default); `kind` is "sqlite" or "postgres"."""

    kind: str
    engine: object | None = None
    key: int = SCHEDULER_LOCK_KEY
    _conn: object | None = field(default=None, repr=False)
    _held: bool = False
    _told_standby: bool = False

    @property
    def held(self) -> bool:
        return self._held

    def _engine(self):
        if self.engine is None:
            from app.db.session import engine

            self.engine = engine
        return self.engine

    async def ensure(self) -> bool:
        """True when this process holds the scheduler lock (taking it if it is
        free). Never raises: a database that cannot be reached is "not held"."""
        if self.kind != "postgres":
            self._held = True
            return True
        try:
            if self._held:
                if await self._still_connected():
                    return True
                logger.warning("lost the routine scheduler lock (its database connection broke)")
                await self._drop()
            if self._conn is None:
                self._conn = await self._engine().connect()
            got = bool(await self._conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": self.key}))
            await self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not check the routine scheduler lock", error=exc.__class__.__name__)
            await self._drop()
            return False
        if got:
            self._held = True
            self._told_standby = False
            logger.info("this backend runs the routine scheduler")
        elif not self._told_standby:
            self._told_standby = True
            logger.info("another backend on this database runs the routine scheduler; this one stands by")
        return got

    async def _still_connected(self) -> bool:
        try:
            await self._conn.scalar(text("SELECT 1"))
            await self._conn.commit()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _drop(self) -> None:
        conn, self._conn, self._held = self._conn, None, False
        if conn is not None:
            try:
                await conn.invalidate()
            except Exception:  # noqa: BLE001
                pass
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass

    async def release(self) -> None:
        """Give the lock up (clean shutdown). Never raises."""
        if self.kind != "postgres":
            self._held = False
            return
        conn, held = self._conn, self._held
        self._conn, self._held = None, False
        if conn is None:
            return
        try:
            if held:
                await conn.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
                await conn.commit()
        except Exception:  # noqa: BLE001
            pass  # the session ends below, which releases it anyway
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass
