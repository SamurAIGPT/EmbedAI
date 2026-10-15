"""The launcher's one-line message: what is accepted, what is refused, and that a
refusal never repeats a secret (D-012 decision 3)."""

import io
import json
import os
import threading
from pathlib import Path

import pytest

from app.desktop import handshake

TOKEN = "SECRET-TOKEN-" + "t" * 30
KEY = "k" * 43 + "="
OTHER_KEY = "j" * 43 + "="


def message(tmp_path: Path, **changes) -> dict:
    base = {
        "version": 1,
        "token": TOKEN,
        "encryption_key": KEY,
        "data_dir": str(tmp_path / "data"),
        "ui_dir": str(tmp_path / "ui"),
        "claude_cli_path": "C:/tools/claude.exe",
    }
    merged = {**base, **changes}
    return {name: value for name, value in merged.items() if value is not ...}


def line(tmp_path: Path, **changes) -> bytes:
    return (json.dumps(message(tmp_path, **changes)) + "\n").encode()


def test_a_complete_message_is_accepted(tmp_path):
    result = handshake.parse(line(tmp_path))
    assert result.token == TOKEN
    assert result.encryption_key == KEY
    assert result.data_dir == tmp_path / "data"
    assert result.ui_dir == tmp_path / "ui"
    assert result.claude_cli_path == "C:/tools/claude.exe"


def test_the_ui_folder_and_the_claude_path_are_optional(tmp_path):
    result = handshake.parse(line(tmp_path, ui_dir=..., claude_cli_path=...))
    assert result.ui_dir is None
    assert result.claude_cli_path == ""


def test_a_list_of_keys_is_accepted_like_the_settings_list(tmp_path):
    assert handshake.parse(line(tmp_path, encryption_key=f"{KEY},{OTHER_KEY}")).encryption_key == f"{KEY},{OTHER_KEY}"


def test_what_the_message_holds_is_not_printed(tmp_path):
    shown = repr(handshake.parse(line(tmp_path)))
    assert TOKEN not in shown and KEY not in shown


@pytest.mark.parametrize(
    "changes, mentions",
    [
        ({"version": 2}, "protocol"),
        ({"version": ...}, "protocol"),
        ({"token": ...}, "token"),
        ({"token": "short"}, "token"),
        ({"token": "has a space " + "t" * 30}, "token"),
        ({"token": 12345}, "token"),
        ({"encryption_key": ...}, "encryption_key"),
        ({"encryption_key": "not-a-fernet-key"}, "encryption_key"),
        ({"encryption_key": f"{KEY},broken"}, "encryption_key"),
        ({"data_dir": ...}, "data_dir"),
        ({"data_dir": "relative/path"}, "data_dir"),
        ({"data_dir": ""}, "data_dir"),
        ({"ui_dir": "relative"}, "ui_dir"),
        ({"claude_cli_path": 5}, "claude_cli_path"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_a_wrong_message_is_refused_naming_the_field_and_never_a_value(tmp_path, changes, mentions):
    with pytest.raises(handshake.HandshakeError) as caught:
        handshake.parse(line(tmp_path, **changes))
    assert mentions in str(caught.value) or "protocol" in str(caught.value)
    for secret in (TOKEN, KEY, "not-a-fernet-key", "broken"):
        assert secret not in str(caught.value)


@pytest.mark.parametrize("raw", [b"", b"\n", b"not json\n", b"[1, 2]\n", b'"text"\n', b"\xff\xfe\n", b"null\n"])
def test_a_message_that_is_not_a_json_object_is_refused(raw):
    with pytest.raises(handshake.HandshakeError):
        handshake.parse(raw)


def test_a_data_folder_that_is_a_file_is_refused(tmp_path):
    (tmp_path / "data").write_text("a file", encoding="utf-8")
    with pytest.raises(handshake.HandshakeError, match="not a folder"):
        handshake.parse(line(tmp_path))


def test_read_takes_the_first_line_and_leaves_the_rest(tmp_path):
    stream = io.BytesIO(line(tmp_path) + b"more\n")
    assert handshake.read(stream).token == TOKEN
    assert stream.read() == b"more\n"


def test_read_refuses_a_stream_that_ends_without_a_message():
    with pytest.raises(handshake.HandshakeError, match="closed"):
        handshake.read(io.BytesIO(b""))


def test_read_refuses_a_line_that_never_ends(tmp_path):
    with pytest.raises(handshake.HandshakeError, match="too long or has no end"):
        handshake.read(io.BytesIO(line(tmp_path).rstrip(b"\n")))


def test_read_refuses_a_line_that_is_too_long():
    with pytest.raises(handshake.HandshakeError, match="too long"):
        handshake.read(io.BytesIO(b"x" * (handshake.MAX_LINE_BYTES + 10) + b"\n"))


def test_read_gives_up_on_a_launcher_that_says_nothing():
    read_end, write_end = os.pipe()
    stream = os.fdopen(read_end, "rb")
    try:
        with pytest.raises(handshake.HandshakeError, match="nothing within"):
            handshake.read(stream, timeout=0.3)
    finally:
        os.close(write_end)  # lets the abandoned reading thread finish
        stream.close()


def test_read_does_not_hang_the_process_on_exit_because_its_thread_is_a_daemon():
    read_end, write_end = os.pipe()
    stream = os.fdopen(read_end, "rb")
    try:
        with pytest.raises(handshake.HandshakeError):
            handshake.read(stream, timeout=0.2)
        assert all(t.daemon for t in threading.enumerate() if t.name == "handshake-read")
    finally:
        os.close(write_end)
        stream.close()
