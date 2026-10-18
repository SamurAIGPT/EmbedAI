"""Column types that behave the same on PostgreSQL and SQLite.

On PostgreSQL both types compile to exactly what the models used before
(BIGINT identity columns, TIMESTAMP WITH TIME ZONE), so the existing schema
and migrations are unchanged. The SQLite differences are handled here and
nowhere else:

- SQLite only auto-increments a column declared as INTEGER PRIMARY KEY, so
  the identity key becomes INTEGER there.
- SQLite has no timezone-aware datetime. Values are stored as naive UTC and
  come back timezone-aware, which is what the services expect (they compare
  against `datetime.now(UTC)`). A naive datetime is taken to be UTC.
"""

from datetime import UTC

from sqlalchemy import JSON, BigInteger, DateTime, Integer
from sqlalchemy.types import TypeDecorator

BigIntPK = BigInteger().with_variant(Integer, "sqlite")

# JSON stored as TEXT on SQLite and JSON on PostgreSQL. Python None is stored
# as SQL NULL (not the JSON value null), so "no value" has one representation.
# Values are replaced whole, never mutated in place.
JSONValue = JSON(none_as_null=True)


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or dialect.name != "sqlite":
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None or dialect.name != "sqlite":
            return value
        return value.replace(tzinfo=UTC)
