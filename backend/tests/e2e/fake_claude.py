"""A `claude` executable for tests that start the real backend process.

The backend calls it exactly as it calls the real CLI (app/services/
local_claude_runner.py): `--version`, `auth status --json`, and turns with
`-p --session-id/--resume ...`. Status calls are answered here; a turn is
handed to tests/services/fake_claude_cli.py, which plays the script in
$FAKE_CLAUDE_PLAN. A `--mcp-config` value is saved next to the plan as
mcp.json, where that script looks for it.
"""

import json
import os
import sys
from pathlib import Path

FAKE_CLI = Path(__file__).parents[1] / "services" / "fake_claude_cli.py"


def value_after(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def main(args: list[str]) -> int:
    if args[:1] == ["--version"]:
        print("2.1.999 (Claude Code, test double)")
        return 0
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": os.environ.get("FAKE_CLAUDE_SIGNED_IN", "1") == "1"}))
        return 0
    plan = os.environ.get("FAKE_CLAUDE_PLAN")
    if not plan:
        print("FAKE_CLAUDE_PLAN is not set", file=sys.stderr)
        return 2
    resume = value_after(args, "--resume")
    session = resume or value_after(args, "--session-id")
    mcp = value_after(args, "--mcp-config")
    if mcp:
        Path(plan).with_name("mcp.json").write_text(mcp, encoding="utf-8")
    tools = "--input-format" in args
    argv = [sys.executable, str(FAKE_CLI), plan, "resume" if resume else "new", session or "none", "1" if tools else "0"]
    mode = value_after(args, "--permission-mode")
    if mode:
        argv.append(mode)
    os.execv(sys.executable, argv)
    return 0  # not reached


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
