"""The SQLite work must not change what PostgreSQL gets.

`postgres_schema.sql` was generated from the models before the dialect-aware
types were introduced; the models must still compile to exactly that DDL."""

from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db import models  # noqa: F401
from app.db.base import Base

SNAPSHOT = Path(__file__).with_name("postgres_schema.sql")


def postgres_ddl() -> str:
    dialect = postgresql.dialect()
    statements = []
    for table in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda i: i.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    return "\n\n".join(statements) + "\n"


def test_models_still_compile_to_the_original_postgres_schema():
    expected = SNAPSHOT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert postgres_ddl() == expected
