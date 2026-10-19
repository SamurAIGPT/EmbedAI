"""The systemd unit maker (app/server/service.py): the text it makes, and what it refuses."""

import configparser
import sys
from pathlib import Path

import pytest

from app.server import service
from app.server.service import ServiceError, main, render


def parse(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(text)
    return parser


def unit(**kwargs):
    defaults = dict(user="opendots", python="/opt/open-dots/backend/.venv/bin/python", backend=Path("/opt/open-dots/backend"))
    return render(**{**defaults, **kwargs})


def test_the_unit_runs_the_server_from_the_backend_folder_as_the_account():
    text = unit()
    sections = parse(text)
    run = sections["Service"]
    assert run["User"] == "opendots"
    assert run["WorkingDirectory"] == "/opt/open-dots/backend"
    assert run["EnvironmentFile"] == "/etc/open-dots/open-dots.env"
    assert run["ExecStart"] == "/opt/open-dots/backend/.venv/bin/python -m app.server"
    assert run["Restart"] == "on-failure" and run["NoNewPrivileges"] == "true"
    assert sections["Install"]["WantedBy"] == "multi-user.target"
    assert sections["Unit"]["After"] == "network-online.target"


def test_it_migrates_the_database_before_every_start():
    run = parse(unit())["Service"]
    assert run["ExecStartPre"] == "/opt/open-dots/backend/.venv/bin/python -m app.server migrate"


def test_a_stop_is_given_time_to_end_conversations():
    assert parse(unit())["Service"]["TimeoutStopSec"] == str(service.STOP_SECONDS)


def test_the_settings_file_can_be_named():
    assert parse(unit(env_file="/srv/dots/dots.env"))["Service"]["EnvironmentFile"] == "/srv/dots/dots.env"


def test_no_secret_or_setting_value_is_written_into_the_unit():
    text = unit()
    assert "TOKEN_ENCRYPTION_KEYS" not in text
    lines = [line for line in text.splitlines() if line.startswith("Environment")]
    assert lines == ["EnvironmentFile=/etc/open-dots/open-dots.env"]  # values live in the file, never in the unit


@pytest.mark.skipif(sys.platform == "win32", reason="the unit takes this machine's Python path, which is a Linux path")
def test_by_default_it_uses_this_python_and_this_backend_folder():
    text = render(user="opendots")
    assert f"ExecStart={sys.executable} -m app.server" in text
    assert f"WorkingDirectory={service.backend_dir().as_posix()}" in text


@pytest.mark.parametrize("bad", ["", "Root", "a b", "x;y", "9lives", "-dash", "a" * 40, "ro/ot"])
def test_a_bad_account_name_is_refused(bad):
    with pytest.raises(ServiceError, match="account"):
        unit(user=bad)


@pytest.mark.parametrize("bad", ["relative/python", "/opt/with space/python", "/opt/50%/python", "/opt/$HOME/python",
                                 '/opt/"quoted"/python', "/opt/a;b/python", "/opt/back\\slash/python"])
def test_a_path_systemd_would_misread_is_refused(bad):
    with pytest.raises(ServiceError, match="Python path"):
        unit(python=bad)
    with pytest.raises(ServiceError, match="settings file"):
        unit(env_file=bad)


def test_a_backend_folder_with_a_space_is_refused():
    with pytest.raises(ServiceError, match="backend folder"):
        unit(backend=Path("/opt/my app/backend"))


@pytest.mark.skipif(sys.platform == "win32", reason="the unit takes this machine's Python path, which is a Linux path")
def test_printing_is_the_default_and_writes_nothing(capsys, tmp_path):
    assert main(["--user", "opendots"]) == 0
    out = capsys.readouterr().out
    assert "[Service]" in out and "ExecStartPre=" in out


def test_a_bad_option_is_a_usage_error(capsys):
    assert main(["--user", "Not Valid"]) == 2
    assert "account name" in capsys.readouterr().err
    assert main(["--name", "bad name"]) == 2
    assert main(["--no-such-flag"]) == 2


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="installing a unit is Linux only")
def test_write_makes_the_file_and_never_replaces_one(tmp_path, capsys):
    target = tmp_path / "open-dots.service"
    assert main(["--user", "opendots", "--write", str(target)]) == 0
    out = capsys.readouterr().out
    assert target.read_text().startswith("# Open Dots") and "systemctl enable --now open-dots" in out
    assert (target.stat().st_mode & 0o777) == 0o644
    before = target.read_text()
    assert main(["--user", "opendots", "--write", str(target)]) == 4
    assert "already exists" in capsys.readouterr().err
    assert target.read_text() == before


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="only checks the message on other systems")
@pytest.mark.skipif(sys.platform == "win32", reason="the unit takes this machine's Python path, which is a Linux path")
def test_write_says_systemd_is_linux_only_elsewhere(tmp_path, capsys):
    assert main(["--user", "opendots", "--write", str(tmp_path / "x.service")]) == 2
    assert "Linux only" in capsys.readouterr().err
    assert not (tmp_path / "x.service").exists()


def test_the_instructions_name_the_commands_and_the_doctor():
    text = service.instructions("open-dots", "/etc/systemd/system/open-dots.service", "/etc/open-dots/open-dots.env", "opendots")
    for expected in ("systemctl daemon-reload", "systemctl enable --now open-dots", "journalctl -u open-dots -f", "python -m app.server doctor", "0600"):
        assert expected in text, expected


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the printed commands are for Linux")
def test_the_printed_commands_never_empty_an_existing_settings_file(tmp_path):
    import getpass
    import subprocess

    env_file = tmp_path / "etc" / "open-dots.env"
    text = service.instructions("open-dots", "/x/open-dots.service", str(env_file), getpass.getuser())
    commands = [line.strip() for line in text.splitlines() if line.strip().startswith(("install ", "test -e"))]
    assert len(commands) == 2

    def run():
        for command in commands:
            subprocess.run(["sh", "-c", command], check=True)

    run()  # a new file: made, empty, private
    assert env_file.read_text() == "" and (env_file.stat().st_mode & 0o777) == 0o600
    env_file.write_text("TOKEN_ENCRYPTION_KEYS=keep-me\n")
    run()  # running the steps again, as after an upgrade
    assert env_file.read_text() == "TOKEN_ENCRYPTION_KEYS=keep-me\n"
