from pathlib import Path

import pytest

from app.services import local_claude_runner as runner
from app.services.approval_modes import (
    DEFAULT_MODE,
    MODES,
    UnknownApprovalMode,
    cli_permission_mode,
    validate_mode,
)


def test_modes_map_onto_the_cli_permission_modes():
    assert cli_permission_mode("ask") == "default"
    assert cli_permission_mode("accept_edits") == "acceptEdits"
    assert cli_permission_mode("plan") == "plan"


def test_not_chosen_is_ask_and_there_is_no_full_access():
    assert validate_mode(None) == DEFAULT_MODE == "ask"
    assert cli_permission_mode(None) == "default"
    assert "full" not in MODES and "bypass" not in " ".join(MODES)


@pytest.mark.parametrize("bad", ["", "full", "bypassPermissions", "dontAsk", "ASK"])
def test_an_unknown_mode_is_refused(bad):
    with pytest.raises(UnknownApprovalMode):
        validate_mode(bad)
    with pytest.raises(UnknownApprovalMode):
        cli_permission_mode(bad)


def _mode_in(args):
    return args[args.index("--permission-mode") + 1]


def test_a_tools_run_passes_the_chosen_mode_to_the_cli():
    exe = Path("claude.exe")
    assert _mode_in(runner.build_args(exe, "s", tools=True)) == "default"
    assert _mode_in(runner.build_args(exe, "s", tools=True, approval_mode="ask")) == "default"
    assert _mode_in(runner.build_args(exe, "s", tools=True, approval_mode="accept_edits")) == "acceptEdits"
    assert _mode_in(runner.build_args(exe, "s", tools=True, approval_mode="plan")) == "plan"
    # Exactly one --permission-mode, and the rest of the tools line is intact.
    args = runner.build_args(exe, "s", tools=True, approval_mode="plan")
    assert args.count("--permission-mode") == 1
    assert "--permission-prompt-tool" in args and "--strict-mcp-config" in args


def test_the_shared_tools_constant_is_never_changed():
    runner.build_args(Path("claude.exe"), "s", tools=True, approval_mode="plan")
    assert runner.TOOLS_ARGS[runner.TOOLS_ARGS.index("--permission-mode") + 1] == "default"


def test_a_chat_only_run_stays_locked_down_whatever_the_mode():
    args = runner.build_args(Path("claude.exe"), "s", approval_mode="accept_edits")
    assert _mode_in(args) == "dontAsk" and "--tools" in args


def test_an_unknown_mode_never_reaches_the_cli():
    with pytest.raises(UnknownApprovalMode):
        runner.build_args(Path("claude.exe"), "s", tools=True, approval_mode="full")
