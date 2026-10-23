"""`python -m app.server doctor` (app/server/doctor.py): what it finds in each state,
that it never changes anything, and its exit codes."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.server import doctor
from app.server.doctor import FAIL, INFO, OK, WARN, Check
from app.server.entry import main as server_main
from tests.conftest import BACKEND_DIR, using_sqlite
from tests.db.sqlite_helpers import run_alembic, one_revision_back

pytestmark = pytest.mark.skipif(not using_sqlite(), reason="these checks use their own SQLite files")

READY_ENGINE = {"id": "claude", "label": "Claude", "available": True, "installed": True, "signed_in": True, "version": "2.1.0", "reason": None}


def by_name(checks, name) -> Check:
    return next(c for c in checks if c.name == name)


@pytest.fixture(autouse=True)
def execution_on(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)  # not whatever this machine's .env says


@pytest.fixture
def ui(tmp_path) -> Path:
    folder = tmp_path / "out"
    for page in ("", "login", "pair", "chat", "settings"):
        (folder / page).mkdir(parents=True, exist_ok=True)
        (folder / page / "index.html").write_text("<html></html>")
    return folder


@pytest.fixture
def target(tmp_path, monkeypatch) -> Path:
    file = tmp_path / "data" / "open-dots.db"
    monkeypatch.setattr(settings, "SQLITE_PATH", str(file))
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOT", str(tmp_path / "work"))
    return file


def environment(ui: Path, **extra) -> dict:
    return {"OPEN_DOTS_UI_DIR": str(ui), "OPEN_DOTS_PORT": "18999", **extra}


# -- individual checks --------------------------------------------------------------------------

def test_pages_complete_and_incomplete(ui):
    assert doctor.check_pages(ui).status == OK
    (ui / "pair" / "index.html").unlink()
    broken = doctor.check_pages(ui)
    assert broken.status == FAIL and "pair/index.html" in broken.detail and "build-ui" in broken.advice
    assert doctor.check_pages(None).status == FAIL


def test_the_public_address_states(monkeypatch):
    monkeypatch.setattr(settings, "OPEN_DOTS_PUBLIC_URL", "")
    assert doctor.check_public_url(settings).status == WARN
    monkeypatch.setattr(settings, "OPEN_DOTS_PUBLIC_URL", "http://dots.example.com")
    assert doctor.check_public_url(settings).status == WARN
    monkeypatch.setattr(settings, "OPEN_DOTS_PUBLIC_URL", "https://dots.example.com")
    assert doctor.check_public_url(settings).status == OK


def test_loopback_trust_on_a_server_is_a_problem(monkeypatch):
    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "off")
    assert by_name(doctor.check_security(settings), "Loopback trust").status == OK
    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "programs")
    assert by_name(doctor.check_security(settings), "Loopback trust").status == FAIL


def test_tools_are_reported_either_way_and_never_as_a_failure(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    off = by_name(doctor.check_security(settings), "Tools")
    assert off.status == INFO and "LOCAL_TOOLS_ENABLED=true" in off.advice
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    assert by_name(doctor.check_security(settings), "Tools").status == INFO


def test_agents_that_cannot_run_are_a_problem_with_the_fix(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    assert by_name(doctor.check_security(settings), "Local execution").status == OK
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", False)
    off = by_name(doctor.check_security(settings), "Local execution")
    assert off.status == FAIL and "LOCAL_EXECUTION_ENABLED=true" in off.advice


def test_the_database_in_each_state(target):
    check, state = doctor.check_database(settings)
    assert state == "new" and check.status == FAIL and "migrate" in check.advice
    assert not target.exists() and not target.parent.exists()  # looking changed nothing

    assert server_main(["migrate"]) == 0
    check, state = doctor.check_database(settings)
    assert state == "current" and check.status == OK

    assert run_alembic(target, "downgrade", one_revision_back()).returncode == 0
    check, state = doctor.check_database(settings)
    assert state == "behind" and check.status == FAIL and "older version" in check.detail


def test_a_foreign_database_file_is_a_problem_and_left_alone(target):
    target.parent.mkdir(parents=True)
    connection = sqlite3.connect(target)
    connection.execute("CREATE TABLE somebody_elses (id INTEGER)")
    connection.commit()
    connection.close()
    before = target.read_bytes()
    check, state = doctor.check_database(settings)
    assert check.status == FAIL and "schema version is unknown" in check.detail and state is None
    assert target.read_bytes() == before


def test_the_key_check_and_the_owner_check(target):
    server_main(["migrate"])
    assert doctor.check_key().status == OK
    owner = doctor.check_owner()
    assert owner.status == WARN and "set-passphrase" in owner.advice

    connection = sqlite3.connect(target)
    connection.execute(
        "INSERT INTO user_credential (user_id, provider, file_path, ciphertext) VALUES ('u1', 'claude', 'f', 'not-a-valid-token')"
    )
    connection.commit()
    connection.close()
    wrong = doctor.check_key()
    assert wrong.status == FAIL and "cannot be decrypted" in wrong.detail


def test_folders_that_can_be_written_are_ok_without_being_created(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "newdata" / "x.db"))
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOT", str(tmp_path / "newwork"))
    checks = doctor.check_folders(settings)
    assert {c.name for c in checks} == {"Workspace folder", "Database folder"} and all(c.status == OK for c in checks)
    assert not (tmp_path / "newdata").exists() and not (tmp_path / "newwork").exists()


def test_a_folder_that_cannot_be_written_is_a_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "data" / "x.db"))
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOT", str(tmp_path / "work"))
    monkeypatch.setattr(os, "access", lambda *a, **k: False)
    checks = doctor.check_folders(settings)
    assert all(c.status == FAIL and "cannot be written" in c.detail for c in checks)


# -- engines -------------------------------------------------------------------------------------

def make_describe(*entries):
    async def describe():
        return list(entries)

    return describe


def test_one_ready_engine_is_enough_and_the_others_are_only_information():
    checks = doctor.check_engines(make_describe(
        READY_ENGINE,
        {"id": "codex", "label": "Codex", "available": False, "installed": False, "signed_in": None, "reason": "Codex CLI not found.", "version": None},
    ))
    assert by_name(checks, "Engine Claude").status == OK
    assert by_name(checks, "Engine Codex").status == INFO
    assert by_name(checks, "Engines").status == OK and "Claude" in by_name(checks, "Engines").detail


def test_no_ready_engine_is_a_problem_with_the_sign_in_command():
    checks = doctor.check_engines(make_describe(
        {"id": "claude", "label": "Claude", "available": False, "installed": True, "signed_in": False, "reason": "Not signed in.", "version": "2.1.0"},
    ))
    assert by_name(checks, "Engines").status == FAIL
    assert "claude auth login" in by_name(checks, "Engine Claude").advice


def test_a_probe_that_raises_is_reported_and_does_not_stop_the_check():
    async def broken():
        raise RuntimeError("boom")

    checks = doctor.check_engines(broken)
    assert len(checks) == 1 and checks[0].status == FAIL and "boom" in checks[0].detail


# -- the public address from outside -------------------------------------------------------------

class Reply:
    def __init__(self, status_code=200, text=""):
        self.status_code, self.text = status_code, text


def fetch_with(health=None, login=None):
    def fetch(path):
        reply = health if path.endswith("health") else login
        if isinstance(reply, Exception):
            raise reply
        return reply

    return fetch


def test_the_address_check_passes_when_the_server_answers():
    checks = doctor.check_url("https://dots.example.com", fetch_with(Reply(200, '{"status":"ok"}'), Reply(200, "<html>")))
    assert [c.status for c in checks] == [OK, OK]


def test_the_address_check_names_a_refused_host():
    checks = doctor.check_url("https://dots.example.com", fetch_with(Reply(421, ""), Reply(200)))
    assert checks[0].status == FAIL and "OPEN_DOTS_PUBLIC_URL" in checks[0].advice


def test_the_address_check_notices_a_proxy_that_does_not_reach_the_server():
    checks = doctor.check_url("https://dots.example.com", fetch_with(Reply(200, "<html>welcome to nginx</html>"), Reply(404)))
    assert checks[0].status == FAIL and "somewhere else" in checks[0].advice
    assert any(c.name == "Sign-in page" and c.status == FAIL for c in checks)


def test_the_address_check_explains_a_certificate_problem():
    error = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    checks = doctor.check_url("https://dots.example.com", fetch_with(error, error))
    assert len(checks) == 1 and checks[0].status == FAIL and "certificate" in checks[0].advice.lower()


def test_the_address_check_explains_an_unreachable_name():
    checks = doctor.check_url("https://dots.example.com", fetch_with(httpx.ConnectError("Name or service not known"), None))
    assert "ports 80 and 443" in checks[0].advice


def test_the_address_check_warns_about_plain_http_and_refuses_junk():
    checks = doctor.check_url("http://dots.example.com", fetch_with(Reply(200, '{"status":"ok"}'), Reply(200)))
    assert any(c.name == "Address HTTPS" and c.status == WARN for c in checks)
    assert doctor.check_url("dots.example.com/app")[0].status == FAIL


# -- the whole run ---------------------------------------------------------------------------------

def test_a_healthy_machine_has_no_problems(ui, target, monkeypatch):
    server_main(["migrate"])
    monkeypatch.setattr(settings, "OPEN_DOTS_PUBLIC_URL", "https://dots.example.com")
    checks = doctor.run_checks(environment(ui), describe=make_describe(READY_ENGINE))
    assert not [c for c in checks if c.status == FAIL], [c for c in checks if c.status == FAIL]
    assert by_name(checks, "Database").status == OK and by_name(checks, "Pages").status == OK
    assert by_name(checks, "Engines").status == OK and by_name(checks, "Encryption key").status == OK


def test_an_owner_without_a_passphrase_is_a_warning_not_a_problem(ui, target, monkeypatch):
    server_main(["migrate"])
    checks = doctor.run_checks(environment(ui), describe=make_describe(READY_ENGINE))
    assert by_name(checks, "Owner passphrase").status == WARN
    assert not [c for c in checks if c.status == FAIL]


def test_a_new_machine_lists_what_to_do_and_changes_nothing(ui, target, monkeypatch):
    checks = doctor.run_checks(environment(ui))
    problems = {c.name for c in checks if c.status == FAIL}
    assert problems == {"Database"}
    assert by_name(checks, "Engines").status == INFO and "not ready" in by_name(checks, "Engines").detail
    assert not target.exists() and not target.parent.exists()


def test_without_pages_that_is_the_problem(target, tmp_path):
    empty = tmp_path / "nopages"
    empty.mkdir()
    checks = doctor.run_checks({"OPEN_DOTS_UI_DIR": str(empty)})
    assert by_name(checks, "Pages").status == FAIL and by_name(checks, "Server options").status == FAIL


def test_render_and_exit_codes(ui, target, capsys):
    assert doctor.main([], environment(ui)) == 1  # the database is new
    out = capsys.readouterr().out
    assert "Open Duet server check" in out and "FAIL  Database" in out and "-> Run `python -m app.server migrate`" in out
    assert "1 problem(s)" in out and "Fix the problems above first." in out


def test_json_output_is_a_list_of_checks(ui, target, capsys):
    doctor.main(["--json"], environment(ui))
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and {"name", "status", "detail", "advice"} <= set(data[0])
    assert next(c for c in data if c["name"] == "Database")["status"] == FAIL


def test_the_doctor_is_reachable_through_the_server_command(ui, target, capsys):
    assert server_main(["doctor", "--json"], environment(ui)) == 1
    assert json.loads(capsys.readouterr().out)


def test_a_missing_encryption_key_is_reported_by_name_without_a_traceback_or_a_secret(tmp_path):
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG") if k in os.environ}
    env.update(PYTHONPATH=str(BACKEND_DIR), PYTHONUTF8="1", HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               DATABASE_BACKEND="sqlite", SQLITE_PATH=str(tmp_path / "x.db"))
    result = subprocess.run([sys.executable, "-m", "app.server", "doctor"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 1, result.stderr
    assert "TOKEN_ENCRYPTION_KEYS" in result.stdout and "FAIL  Settings" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_preset_advice_only_for_installed_signed_out_engines():
    entries = [
        {"id": engine, "label": engine, "available": False, "installed": True, "signed_in": False, "reason": "Connect this engine.", "version": None}
        for engine in ("gemini", "cursor", "opencode", "openrouter")
    ]
    checks = doctor.check_engines(make_describe(*entries))
    assert "gemini" in by_name(checks, "Engine gemini").advice
    assert "cursor-agent login" in by_name(checks, "Engine cursor").advice
    assert "opencode auth login" in by_name(checks, "Engine opencode").advice
    assert "Settings" in by_name(checks, "Engine openrouter").advice
    assert by_name(checks, "Engines").status == FAIL
