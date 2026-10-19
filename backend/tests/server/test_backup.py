"""`python -m app.server backup` and `restore` (app/server/backup.py): a round trip that
loses nothing, and a restore that refuses anything unsafe before it changes anything."""

import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.core import crypto, data_lease
from app.core.config import settings
from app.server import backup
from app.server.backup_cli import EXIT_DATA, EXIT_KEY_MISMATCH
from app.server.entry import main as server_main
from tests.conftest import BACKEND_DIR, using_sqlite

pytestmark = pytest.mark.skipif(not using_sqlite(), reason="these checks use their own SQLite files")
POSIX = sys.platform != "win32"


def point_at(monkeypatch, root: Path) -> dict[str, Path]:
    """An installation whose every part lives under `root`."""
    monkeypatch.setattr(settings, "SQLITE_PATH", str(root / "data" / "open-dots.db"))
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(root / "attachments"))
    monkeypatch.setattr(settings, "DECISION_LOG_DIR", str(root / "decisions"))
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOT", str(root / "work"))
    return backup.locations()


def fill(where: dict[str, Path], *, foreign_key: bool = False) -> None:
    assert server_main(["migrate"]) == 0
    connection = sqlite3.connect(where["database"])
    token = crypto.encrypt("a stored token")
    if foreign_key:
        token = Fernet(Fernet.generate_key()).encrypt(b"x").decode()
    connection.execute("INSERT INTO user_credential (user_id, provider, file_path, ciphertext) VALUES ('u1', 'claude', 'f', ?)", (token,))
    connection.commit()
    connection.close()
    files = {
        "attachments/7/pic.png": b"\x89PNG not really",
        "decisions/2026-10.ndjson": b'{"decision": "allowed"}\n',
        "memory-state/journal/1.ndjson": b"{}\n",
        "workspace/agents/1/MEMORY.md": b"# notes\nremember the pelican\n",
        "workspace/agents/1/project/run.sh": b"#!/bin/sh\necho hi\n",
    }
    for name, data in files.items():
        part, _, rest = name.partition("/")
        path = where[part] / rest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if POSIX:
        (where["workspace"] / "agents/1/project/run.sh").chmod(0o755)
        os.symlink("/etc/passwd", where["workspace"] / "agents/1/link-out")
    (where["workspace"] / "agents/2").mkdir(parents=True)  # an empty folder is kept too


def read_tree(where: dict[str, Path]) -> dict[str, bytes]:
    out = {}
    for name in backup.PARTS[1:]:
        for path in where[name].rglob("*") if where[name].exists() else []:
            if path.is_file() and not path.is_symlink():
                out[f"{name}/{path.relative_to(where[name]).as_posix()}"] = path.read_bytes()
    return out


@pytest.fixture
def source(tmp_path, monkeypatch):
    where = point_at(monkeypatch, tmp_path / "source")
    fill(where)
    return where


def members(archive: Path) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(archive, "r:gz") as tar:
        return {m.name: m for m in tar.getmembers()}


# -- backup ---------------------------------------------------------------------------------------------

def test_a_backup_holds_every_part_with_a_manifest_and_leaves_the_source_alone(source, tmp_path):
    before = read_tree(source)
    report = backup.create_backup(tmp_path / "out")
    assert report.archive.parent == (tmp_path / "out").resolve() and report.archive.name.startswith("open-dots-backup-")
    found = members(report.archive)
    assert backup.MANIFEST in found and "database/open-dots.db" in found
    for name in ("attachments/7/pic.png", "decisions/2026-10.ndjson", "memory-state/journal/1.ndjson",
                 "workspace/agents/1/MEMORY.md", "workspace/agents/1/project/run.sh", "workspace/agents/2"):
        assert name in found, name
    assert read_tree(source) == before
    with tarfile.open(report.archive) as tar:
        manifest = json.load(tar.extractfile(backup.MANIFEST))
    assert manifest["format"] == backup.FORMAT and manifest["database_revision"] == manifest["code_revision"]
    assert "workspace/agents/1/MEMORY.md" in manifest["files"] and manifest["workspaces_included"] is True
    assert report.parts["workspace"].files == 2 and report.parts["database"].files == 1


def test_the_archive_is_private_and_carries_no_owner_or_key(source, tmp_path):
    report = backup.create_backup(tmp_path / "out")
    if POSIX:
        assert stat.S_IMODE(report.archive.stat().st_mode) == 0o600
    for info in members(report.archive).values():
        assert info.uid == 0 and info.gid == 0 and info.uname == "" and info.gname == ""
        assert stat.S_IMODE(info.mode) in (0o600, 0o700)
    raw = report.archive.read_bytes()
    assert settings.TOKEN_ENCRYPTION_KEYS.encode() not in raw
    with tarfile.open(report.archive) as tar:
        database = tar.extractfile("database/open-dots.db").read()
    assert b"a stored token" not in database  # stored credentials stay encrypted


@pytest.mark.skipif(not POSIX, reason="links need POSIX")
def test_links_are_skipped_and_reported_never_followed(source, tmp_path):
    report = backup.create_backup(tmp_path / "out")
    assert any("link-out" in item for item in report.skipped)
    assert not any("passwd" in name for name in members(report.archive))


def test_the_workspace_can_be_left_out(source, tmp_path):
    report = backup.create_backup(tmp_path / "out", skip_workspaces=True)
    assert not any(name.startswith("workspace") for name in members(report.archive))
    with tarfile.open(report.archive) as tar:
        assert json.load(tar.extractfile(backup.MANIFEST))["workspaces_included"] is False


def test_the_default_folder_is_beside_the_database(source):
    report = backup.create_backup()
    assert report.archive.parent == source["database"].parent / "backups"


def test_a_backup_is_refused_when_its_folder_is_inside_what_it_copies(source):
    with pytest.raises(backup.BackupError, match="inside the workspace"):
        backup.create_backup(source["workspace"] / "agents")


def test_no_database_no_backup(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path / "empty")
    with pytest.raises(backup.BackupError, match="nothing to back up"):
        backup.create_backup(tmp_path / "out")


def test_postgresql_is_pointed_at_pg_dump(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATABASE_BACKEND", "postgres")
    with pytest.raises(backup.BackupError, match="pg_dump"):
        backup.create_backup(tmp_path / "out")


HOLD_THE_LEASE = """
import sys, time
from pathlib import Path
from app.core import data_lease
data_lease.acquire(Path(sys.argv[1]))
print("held", flush=True)
time.sleep(120)
"""


@pytest.fixture
def other_process_holds(request):
    """Another process holding the data folder's lease, as a running server does."""
    def hold(database: Path):
        env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR), "PYTHONUTF8": "1"}
        process = subprocess.Popen(
            [sys.executable, "-c", HOLD_THE_LEASE, str(database)],
            cwd=BACKEND_DIR, env=env, stdout=subprocess.PIPE, text=True,
        )
        assert process.stdout.readline().strip() == "held"
        request.addfinalizer(lambda: (process.kill(), process.wait(), process.stdout.close()))
        return process

    return hold


def test_a_running_server_is_noted_not_refused(source, tmp_path, other_process_holds):
    other_process_holds(source["database"])
    report = backup.create_backup(tmp_path / "out")
    assert report.server_running is True and report.archive.is_file()


def test_a_quiet_server_is_not_reported_as_running(source, tmp_path):
    assert backup.create_backup(tmp_path / "out").server_running is False


# -- restore ---------------------------------------------------------------------------------------------

def test_a_round_trip_loses_nothing(source, tmp_path, monkeypatch):
    before = read_tree(source)
    archive = backup.create_backup(tmp_path / "out").archive
    target = point_at(monkeypatch, tmp_path / "fresh")
    report = backup.restore_backup(archive)

    assert read_tree(target) == before
    assert (target["workspace"] / "agents" / "2").is_dir()
    connection = sqlite3.connect(target["database"])
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        (token,) = connection.execute("SELECT ciphertext FROM user_credential").fetchone()
    finally:
        connection.close()
    assert crypto.decrypt(token) == "a stored token"
    assert report.revision and report.older_than_code is False and report.parts["workspace"].files == 2
    assert not [p for p in target["database"].parent.iterdir() if p.name.startswith(".restore-")]
    if POSIX:
        assert stat.S_IMODE((target["workspace"] / "agents/1/project/run.sh").stat().st_mode) == 0o700
        assert stat.S_IMODE((target["workspace"] / "agents/1/MEMORY.md").stat().st_mode) == 0o600
        assert stat.S_IMODE(target["database"].stat().st_mode) == 0o600
    # and the restored installation is a working one
    assert server_main(["migrate"]) == 0


@pytest.mark.skipif(not POSIX, reason="hard links need POSIX")
def test_a_file_with_two_names_is_stored_as_two_files_and_restores(source, tmp_path, monkeypatch):
    os.link(source["workspace"] / "agents/1/MEMORY.md", source["workspace"] / "agents/1/same-file.md")
    before = read_tree(source)
    report = backup.create_backup(tmp_path / "out")
    assert not report.skipped or all("link-out" in item for item in report.skipped)
    found = members(report.archive)
    assert found["workspace/agents/1/MEMORY.md"].isreg() and found["workspace/agents/1/same-file.md"].isreg()
    target = point_at(monkeypatch, tmp_path / "fresh")
    backup.restore_backup(report.archive)
    assert read_tree(target) == before
    assert (target["workspace"] / "agents/1/same-file.md").read_bytes() == before["workspace/agents/1/MEMORY.md"]


def put_row(database: Path, table: str, workspace_path: str) -> None:
    """A row of `table` with this workspace path; other required columns get a placeholder of the right kind."""
    connection = sqlite3.connect(database)
    try:
        values = {"workspace_path": workspace_path}
        for _, name, kind, notnull, default, _pk in connection.execute(f"PRAGMA table_info({table})").fetchall():
            if name in values or name == "id" or not notnull or default is not None:
                continue
            kind = kind.upper()
            values[name] = 0 if ("INT" in kind or "BOOL" in kind) else ("2026-01-01 00:00:00" if ("DATE" in kind or "TIME" in kind) else "x")
        columns = ", ".join(values)
        connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' * len(values))})", list(values.values()))
        connection.commit()
    finally:
        connection.close()


def stored_paths(database: Path) -> dict[str, list[str]]:
    connection = sqlite3.connect(database)
    try:
        return {t: sorted(r[0] for r in connection.execute(f"SELECT workspace_path FROM {t}")) for t in backup.WORKSPACE_TABLES}
    finally:
        connection.close()


def test_a_restore_to_another_workspace_folder_moves_the_paths_inside_it(source, tmp_path, monkeypatch):
    old = source["workspace"]
    outside = str(tmp_path / "elsewhere" / "project")
    for table in backup.WORKSPACE_TABLES:
        put_row(source["database"], table, str(old / "agents" / "1" / "project"))
    put_row(source["database"], "thread", str(old))  # the root itself
    put_row(source["database"], "thread", outside)
    archive = backup.create_backup(tmp_path / "out").archive

    target = point_at(monkeypatch, tmp_path / "fresh")
    report = backup.restore_backup(archive)

    new = target["workspace"]
    paths = stored_paths(target["database"])
    assert paths["agent"] == [str(new / "agents" / "1" / "project")]
    assert paths["computer"] == [str(new / "agents" / "1" / "project")]
    assert sorted(paths["thread"]) == sorted([str(new / "agents" / "1" / "project"), str(new), outside])
    assert report.moved_from == str(old) and report.rewritten == 4 and report.outside == [outside]
    assert (new / "agents" / "1" / "project" / "run.sh").is_file()  # what the rewritten path points at is there


def test_a_restore_to_the_same_place_rewrites_nothing(source, tmp_path, monkeypatch):
    put_row(source["database"], "thread", str(source["workspace"] / "agents" / "1"))
    archive = backup.create_backup(tmp_path / "out").archive
    shutil.rmtree(source["workspace"].parent.parent)  # the source installation is gone
    target = point_at(monkeypatch, tmp_path / "source")
    report = backup.restore_backup(archive)
    assert report.moved_from is None and report.rewritten == 0 and report.outside == []
    assert stored_paths(target["database"])["thread"] == [str(target["workspace"] / "agents" / "1")]


def test_an_older_backup_without_recorded_roots_restores_without_rewriting(source, tmp_path, monkeypatch):
    put_row(source["database"], "thread", str(source["workspace"]))
    made = backup.create_backup(tmp_path / "out").archive

    def drop_roots(manifest: dict) -> dict:
        manifest.pop("roots")
        return manifest

    archive = repack_manifest(made, tmp_path / "old.tar.gz", drop_roots)
    target = point_at(monkeypatch, tmp_path / "fresh")
    report = backup.restore_backup(archive)
    assert report.moved_from is None
    assert stored_paths(target["database"])["thread"] == [str(source["workspace"])]


def test_the_restored_database_is_a_snapshot_not_the_live_file(source, tmp_path, monkeypatch):
    archive = backup.create_backup(tmp_path / "out").archive
    connection = sqlite3.connect(source["database"])
    connection.execute("DELETE FROM user_credential")
    connection.commit()
    connection.close()
    target = point_at(monkeypatch, tmp_path / "fresh")
    backup.restore_backup(archive)
    connection = sqlite3.connect(target["database"])
    try:
        assert connection.execute("SELECT count(*) FROM user_credential").fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize("taken", ["database", "workspace", "attachments"])
def test_a_restore_never_touches_data_that_is_there(source, tmp_path, monkeypatch, taken):
    archive = backup.create_backup(tmp_path / "out").archive
    target = point_at(monkeypatch, tmp_path / "fresh")
    if taken == "database":
        target["database"].parent.mkdir(parents=True)
        target["database"].write_bytes(b"keep me")
    else:
        target[taken].mkdir(parents=True)
        (target[taken] / "mine.txt").write_text("keep me")
    with pytest.raises(backup.BackupError, match="only fills an empty data folder"):
        backup.restore_backup(archive)
    kept = target["database"] if taken == "database" else target[taken] / "mine.txt"
    assert kept.read_bytes() == b"keep me" or kept.read_text() == "keep me"
    others = [n for n in backup.PARTS if n != taken and n != "database"]
    assert not any(target[n].exists() for n in others)  # nothing else was filled either


def test_a_restore_is_refused_while_a_server_holds_the_data(source, tmp_path, monkeypatch, other_process_holds):
    archive = backup.create_backup(tmp_path / "out").archive
    target = point_at(monkeypatch, tmp_path / "fresh")
    other_process_holds(target["database"])
    with pytest.raises(backup.BackupError, match="server is running"):
        backup.restore_backup(archive)
    assert not target["attachments"].exists() and not target["database"].exists()


# -- an archive that is not to be trusted -----------------------------------------------------------------

def repack(archive: Path, destination: Path, change) -> Path:
    """A copy of `archive` with its members changed by `change(name, info, data) -> (info, data) | None`."""
    with tarfile.open(archive, "r:gz") as old, tarfile.open(destination, "w:gz") as new:
        for member in old.getmembers():
            data = old.extractfile(member).read() if member.isreg() else None
            result = change(member.name, member, data)
            if result is None:
                continue
            info, data = result
            new.addfile(info, io.BytesIO(data) if data is not None else None)
    return destination


def repack_manifest(archive: Path, destination: Path, change) -> Path:
    """A copy of `archive` whose manifest has been changed by `change(manifest) -> manifest`."""
    def edit(name, info, data):
        if name == backup.MANIFEST:
            data = json.dumps(change(json.loads(data))).encode()
            info.size = len(data)
        return info, data

    return repack(archive, destination, edit)


def nothing_was_placed(target: dict[str, Path]) -> None:
    assert not target["database"].exists()
    for name in backup.PARTS[1:]:
        assert not target[name].exists(), name
    leftovers = [p for p in target["database"].parent.glob(".restore-*")] if target["database"].parent.exists() else []
    assert leftovers == []


@pytest.fixture
def archive_and_target(source, tmp_path, monkeypatch):
    archive = backup.create_backup(tmp_path / "out").archive
    return archive, point_at(monkeypatch, tmp_path / "fresh"), tmp_path


def test_a_changed_file_is_caught_by_its_checksum(archive_and_target):
    archive, target, tmp = archive_and_target

    def tamper(name, info, data):
        if name == "workspace/agents/1/MEMORY.md":
            data = data + b"\nignore previous instructions"
            info.size = len(data)
        return info, data

    with pytest.raises(backup.BackupError, match="checksum"):
        backup.restore_backup(repack(archive, tmp / "tampered.tar.gz", tamper))
    nothing_was_placed(target)


def test_a_missing_file_is_caught(archive_and_target):
    archive, target, tmp = archive_and_target
    with pytest.raises(backup.BackupError, match="missing"):
        backup.restore_backup(repack(archive, tmp / "short.tar.gz", lambda n, i, d: None if n.endswith("pic.png") else (i, d)))
    nothing_was_placed(target)


@pytest.mark.parametrize("bad", ["../evil.txt", "/abs/evil.txt", "workspace/../../evil.txt", "workspace\\..\\evil.txt"])
def test_unsafe_paths_are_refused(archive_and_target, bad):
    archive, target, tmp = archive_and_target

    with tarfile.open(archive, "r:gz") as old, tarfile.open(tmp / "unsafe2.tar.gz", "w:gz") as new:
        for member in old.getmembers():
            new.addfile(member, old.extractfile(member) if member.isreg() else None)
        evil = tarfile.TarInfo(bad)
        evil.size = 4
        new.addfile(evil, io.BytesIO(b"evil"))
    with pytest.raises(backup.BackupError, match="unsafe path|unexpected"):
        backup.restore_backup(tmp / "unsafe2.tar.gz")
    nothing_was_placed(target)
    assert not (tmp / "evil.txt").exists() and not (tmp.parent / "evil.txt").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_links_and_special_files_in_an_archive_are_refused(archive_and_target, kind):
    archive, target, tmp = archive_and_target
    with tarfile.open(archive, "r:gz") as old, tarfile.open(tmp / "links.tar.gz", "w:gz") as new:
        for member in old.getmembers():
            new.addfile(member, old.extractfile(member) if member.isreg() else None)
        odd = tarfile.TarInfo("workspace/agents/1/odd")
        odd.type, odd.linkname = kind, "/etc/passwd"
        new.addfile(odd)
    with pytest.raises(backup.BackupError, match="link or special file"):
        backup.restore_backup(tmp / "links.tar.gz")
    nothing_was_placed(target)


def test_something_unexpected_at_the_top_level_is_refused(archive_and_target):
    archive, target, tmp = archive_and_target
    with tarfile.open(archive, "r:gz") as old, tarfile.open(tmp / "extra.tar.gz", "w:gz") as new:
        for member in old.getmembers():
            new.addfile(member, old.extractfile(member) if member.isreg() else None)
        extra = tarfile.TarInfo("etc/cron.d/evil")
        extra.size = 1
        new.addfile(extra, io.BytesIO(b"x"))
    with pytest.raises(backup.BackupError, match="unexpected"):
        backup.restore_backup(tmp / "extra.tar.gz")
    nothing_was_placed(target)


def test_not_an_archive_and_not_a_backup(archive_and_target):
    archive, target, tmp = archive_and_target
    junk = tmp / "junk.tar.gz"
    junk.write_bytes(b"this is not a tar file at all")
    with pytest.raises(backup.BackupError, match="not a readable backup"):
        backup.restore_backup(junk)
    plain = tmp / "plain.tar.gz"
    with tarfile.open(plain, "w:gz") as tar:
        info = tarfile.TarInfo("hello.txt")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"hi"))
    with pytest.raises(backup.BackupError, match="no manifest"):
        backup.restore_backup(plain)
    with pytest.raises(backup.BackupError, match="no backup file"):
        backup.restore_backup(tmp / "nowhere.tar.gz")
    nothing_was_placed(target)


def test_a_truncated_archive_restores_nothing(archive_and_target):
    archive, target, tmp = archive_and_target
    cut = tmp / "cut.tar.gz"
    cut.write_bytes(archive.read_bytes()[: archive.stat().st_size // 2])
    with pytest.raises(backup.BackupError):
        backup.restore_backup(cut)
    nothing_was_placed(target)


def test_a_backup_from_a_newer_version_is_refused(archive_and_target):
    archive, target, tmp = archive_and_target
    # Change the database's recorded revision to one this code has never heard of, keeping the manifest honest.
    import hashlib

    with tarfile.open(archive, "r:gz") as tar:
        db_bytes = tar.extractfile("database/open-dots.db").read()
    scratch = tmp / "newer.db"
    scratch.write_bytes(db_bytes)
    connection = sqlite3.connect(scratch)
    connection.execute("UPDATE alembic_version SET version_num = 'ffffffffffff'")
    connection.commit()
    connection.close()
    newer = scratch.read_bytes()

    def swap(name, info, data):
        if name == "database/open-dots.db":
            info.size = len(newer)
            return info, newer
        if name == backup.MANIFEST:
            manifest = json.loads(data)
            manifest["files"]["database/open-dots.db"] = hashlib.sha256(newer).hexdigest()
            data = json.dumps(manifest).encode()
            info.size = len(data)
        return info, data

    with pytest.raises(backup.BackupError, match="newer version"):
        backup.restore_backup(repack(archive, tmp / "newer.tar.gz", swap))
    nothing_was_placed(target)


def test_a_backup_made_with_another_key_is_refused_by_the_key_check(tmp_path, monkeypatch):
    where = point_at(monkeypatch, tmp_path / "source")
    fill(where, foreign_key=True)
    archive = backup.create_backup(tmp_path / "out").archive
    target = point_at(monkeypatch, tmp_path / "fresh")
    with pytest.raises(backup.KeyProblem, match="TOKEN_ENCRYPTION_KEYS"):
        backup.restore_backup(archive)
    nothing_was_placed(target)


# -- the commands ------------------------------------------------------------------------------------------

def test_the_commands_round_trip_and_say_what_is_not_included(source, tmp_path, monkeypatch, capsys):
    assert server_main(["backup", "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "Backup written to" in out and "TOKEN_ENCRYPTION_KEYS" in out and "engines' own sign-ins" in out
    archive = next((tmp_path / "out").glob("open-dots-backup-*.tar.gz"))
    point_at(monkeypatch, tmp_path / "fresh")
    assert server_main(["restore", str(archive)]) == 0
    assert "Restored" in capsys.readouterr().out
    assert server_main(["restore", str(archive)]) == EXIT_DATA  # it is not empty any more
    assert "only fills an empty data folder" in capsys.readouterr().err


def test_the_restore_command_reports_a_key_problem_with_its_own_exit_code(tmp_path, monkeypatch, capsys):
    where = point_at(monkeypatch, tmp_path / "source")
    fill(where, foreign_key=True)
    archive = backup.create_backup(tmp_path / "out").archive
    point_at(monkeypatch, tmp_path / "fresh")
    assert server_main(["restore", str(archive)]) == EXIT_KEY_MISMATCH
    assert "TOKEN_ENCRYPTION_KEYS" in capsys.readouterr().err
