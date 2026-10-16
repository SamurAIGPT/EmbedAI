"""PostgreSQL must keep getting the schema it always had.

`postgres_schema.sql` was generated from the models before the dialect-aware
types were introduced: the original tables must still compile to exactly that
DDL. The tables added later (threads, F04) have their own snapshot,
`postgres_schema_f04.sql`, so that a change to them is a deliberate, reviewed
edit. This is a compile-level check; it needs no PostgreSQL server."""

import re
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db import models  # noqa: F401
from app.db.base import Base

HERE = Path(__file__).parent
ORIGINAL_SNAPSHOT = HERE / "postgres_schema.sql"
F04_SNAPSHOT = HERE / "postgres_schema_f04.sql"
F04_TABLES = {"thread", "turn", "event", "approval"}
SETTINGS_TABLES = {"app_setting", "setting_audit"}


def postgres_ddl(table_names: set[str]) -> str:
    dialect = postgresql.dialect()
    statements = []
    for table in Base.metadata.sorted_tables:
        if table.name not in table_names:
            continue
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda i: i.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    return "\n\n".join(statements) + "\n"


def snapshot_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def original_tables() -> set[str]:
    return set(re.findall(r"^CREATE TABLE (\w+) \(", snapshot_text(ORIGINAL_SNAPSHOT), re.MULTILINE))


def test_models_still_compile_to_the_original_postgres_schema():
    assert postgres_ddl(original_tables()) == snapshot_text(ORIGINAL_SNAPSHOT)


def test_thread_tables_compile_to_their_reviewed_postgres_schema():
    assert postgres_ddl(F04_TABLES) == snapshot_text(F04_SNAPSHOT)


def test_every_table_is_covered_by_one_of_the_snapshots():
    assert original_tables() | F04_TABLES | SETTINGS_TABLES == set(Base.metadata.tables)
    assert not original_tables() & F04_TABLES


def test_settings_tables_compile_to_their_reviewed_postgres_schema():
    assert postgres_ddl(SETTINGS_TABLES) == snapshot_text(HERE / "postgres_schema_settings.sql")
