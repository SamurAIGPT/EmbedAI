"""Migrations written after the baseline must run on SQLite: add a table, add a
nullable column, change a constraint (batch mode), and downgrade.

The migration under test is a scratch revision that exists only in a copy of the
Alembic folder inside the test's temporary directory; the repository's
migrations are not touched."""

import shutil
import sqlite3

import pytest

from app.db.sqlite_baseline import BASELINE_REVISION
from tests.db.schema_description import describe
from tests.db.sqlite_helpers import BACKEND_DIR, run_alembic
from app.db.engine import create_app_sync_engine

SCRATCH_REVISION = "5c0ffee00001"

SCRATCH_MIGRATION = f'''"""scratch migration for tests"""
import sqlalchemy as sa
from alembic import op

revision = "{SCRATCH_REVISION}"
down_revision = "{BASELINE_REVISION}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scratch_thing",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], name="fk_scratch_thing_task_id_task"),
    )
    op.create_index("ix_scratch_thing_task_id", "scratch_thing", ["task_id"])
    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("scratch_note", sa.String(), nullable=True))
    with op.batch_alter_table("schedule") as batch:
        batch.create_unique_constraint("uq_scratch_schedule_user_prompt", ["user_id", "prompt_text"])


def downgrade() -> None:
    with op.batch_alter_table("schedule") as batch:
        batch.drop_constraint("uq_scratch_schedule_user_prompt", type_="unique")
    with op.batch_alter_table("task") as batch:
        batch.drop_column("scratch_note")
    op.drop_index("ix_scratch_thing_task_id", table_name="scratch_thing")
    op.drop_table("scratch_thing")
'''


@pytest.fixture
def scratch_ini(tmp_path):
    """A copy of the Alembic folder plus one extra revision on top of the baseline."""
    folder = tmp_path / "alembic_copy"
    shutil.copytree(BACKEND_DIR / "alembic", folder / "alembic", ignore=shutil.ignore_patterns("__pycache__"))
    (folder / "alembic" / "versions" / "zz_scratch.py").write_text(SCRATCH_MIGRATION, encoding="utf-8")
    ini = folder / "alembic.ini"
    ini.write_text((BACKEND_DIR / "alembic.ini").read_text(encoding="utf-8"), encoding="utf-8")
    return ini


def version_of(path) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        connection.close()


def schema_of(path):
    engine = create_app_sync_engine(f"sqlite:///{path.as_posix()}")
    try:
        return describe(engine)
    finally:
        engine.dispose()


def test_a_later_migration_upgrades_and_downgrades_on_sqlite(tmp_path, scratch_ini):
    database = tmp_path / "app.db"

    # Start where a new SQLite database starts: the baseline, with a row in it.
    first = run_alembic(database, "upgrade", BASELINE_REVISION, ini=scratch_ini)
    assert first.returncode == 0, first.stderr
    at_baseline = schema_of(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO task (user_id, provider, box_id, prompt_text, status) VALUES ('u', 'claude', 'b', 'keep me', 'queued')"
    )
    connection.commit()
    connection.close()

    # Upgrade: new table, nullable column, new unique constraint.
    up = run_alembic(database, "upgrade", "head", ini=scratch_ini)
    assert up.returncode == 0, up.stderr
    assert version_of(database) == SCRATCH_REVISION
    upgraded = schema_of(database)
    assert "scratch_thing" in upgraded
    assert any(c[0] == "scratch_note" and c[2] is True for c in upgraded["task"]["columns"])  # nullable
    assert any(cols == ("user_id", "prompt_text") for _n, cols in upgraded["schedule"]["unique"])
    assert upgraded["scratch_thing"]["foreign_keys"][0][2] == "task"
    # Existing data and the rest of the schema survive the table rebuilds.
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT prompt_text, scratch_note FROM task").fetchall() == [("keep me", None)]
    foreign_key_problems = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    assert foreign_key_problems == []
    assert {k: v for k, v in upgraded.items() if k not in ("scratch_thing",)}.keys() == at_baseline.keys()

    # Downgrade: back to exactly the baseline schema, data intact.
    down = run_alembic(database, "downgrade", "-1", ini=scratch_ini)
    assert down.returncode == 0, down.stderr
    assert version_of(database) == BASELINE_REVISION
    assert schema_of(database) == at_baseline
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT prompt_text FROM task").fetchall() == [("keep me",)]
    connection.close()

    # And forward again.
    again = run_alembic(database, "upgrade", "head", ini=scratch_ini)
    assert again.returncode == 0, again.stderr
    assert schema_of(database) == upgraded


def test_the_scratch_chain_also_starts_from_an_empty_database(tmp_path, scratch_ini):
    """`upgrade head` on a brand new file goes baseline, then the later revision."""
    database = tmp_path / "fresh.db"
    result = run_alembic(database, "upgrade", "head", ini=scratch_ini)
    assert result.returncode == 0, result.stderr
    assert version_of(database) == SCRATCH_REVISION
    assert "scratch_thing" in schema_of(database)
