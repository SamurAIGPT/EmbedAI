"""Describe a database schema as plain data, so two schemas can be compared."""

import re

from sqlalchemy import inspect

IGNORED_TABLES = {"alembic_version"}


def describe(engine, *, with_defaults: bool = True) -> dict:
    """Columns (type, nullability, default), primary keys, indexes, unique
    constraints and foreign keys of every table, exactly as the database
    reports them."""
    insp = inspect(engine)
    description = {}
    for table in sorted(set(insp.get_table_names()) - IGNORED_TABLES):
        columns = []
        for column in insp.get_columns(table):
            entry = (column["name"], column["type"].compile(dialect=engine.dialect).upper(), column["nullable"])
            if with_defaults:
                entry += (column.get("default"),)
            columns.append(entry)
        description[table] = {
            "columns": sorted(columns, key=lambda c: c[0]),
            "primary_key": insp.get_pk_constraint(table)["constrained_columns"],
            "indexes": sorted(
                (i["name"], tuple(i["column_names"]), bool(i["unique"])) for i in insp.get_indexes(table)
            ),
            "unique": sorted(
                (u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(table)
            ),
            "foreign_keys": sorted(
                (f["name"], tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
                for f in insp.get_foreign_keys(table)
            ),
        }
    return description


_TYPE_FAMILY = {
    "BIGINT": "integer", "INTEGER": "integer", "SMALLINT": "integer",
    "TIMESTAMP WITH TIME ZONE": "datetime", "DATETIME": "datetime",
    "VARCHAR": "text", "TEXT": "text",
    "BOOLEAN": "boolean",
}


def _normal_default(default):
    if default is None:
        return None
    text = str(default).strip().lower()
    # PostgreSQL reports a string default with its cast ('ask'::character
    # varying, ''::text); SQLite reports the bare literal.
    text = re.sub(r"^('(?:[^']|'')*')::[a-z ]+$", r"\1", text)
    if text in ("now()", "current_timestamp"):
        return "now"
    return text


# A boolean default is true/false on PostgreSQL and 1/0 on SQLite.
_BOOLEAN_DEFAULT = {"1": "true", "0": "false", "true": "true", "false": "false"}


def _column_default(name, family, default):
    if name == "id":
        return None
    text = _normal_default(default)
    if family == "boolean" and text is not None:
        return _BOOLEAN_DEFAULT.get(text, text)
    return text


def comparable(description: dict) -> dict:
    """The parts of a schema that must be the same on PostgreSQL and SQLite,
    with the known dialect differences smoothed out: integer and datetime
    type names, now() versus CURRENT_TIMESTAMP, casts on string defaults, true/false versus 1/0 boolean defaults, identity columns (PostgreSQL)
    versus integer primary keys (SQLite), and constraint names the database
    generated itself."""
    result = {}
    for table, info in description.items():
        result[table] = {
            "columns": sorted(
                (
                    name,
                    _TYPE_FAMILY.get(kind, kind),
                    nullable,
                    _column_default(name, _TYPE_FAMILY.get(kind, kind), default),
                )
                for name, kind, nullable, default in info["columns"]
            ),
            "primary_key": info["primary_key"],
            # PostgreSQL also lists the index behind each unique constraint as an
            # index; SQLite reports only the constraint. Compare constraints once.
            "indexes": sorted(
                (name, columns, unique)
                for name, columns, unique in info["indexes"]
                if not (unique and columns in {cols for _n, cols in info["unique"]})
            ),
            # An automatically named unique constraint has no name on SQLite.
            "unique": sorted(columns for _name, columns in info["unique"]),
            "foreign_keys": sorted((cols, target, refs) for _n, cols, target, refs in info["foreign_keys"]),
        }
    return result
