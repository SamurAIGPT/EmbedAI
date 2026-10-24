"""Commands the person chose "always allow" for.

Stored in `permission_rule` (no new table): bucket `local_command`, `connector`
holds the match kind and the folder the rule applies in (`exact` or
`exact@/path/to/workspace`, likewise `prefix`), `tool` holds the command text,
and `decision` is always `allow`. Only the Bash tool is covered, and only here:
a computer's `exec` tool
(docs/COMPUTERS.md) is matched the same way, as a shell command; anything that
does not match still asks, and a command on the safety floor
(services/safety_floor.py) always asks, whatever rule it matches.

  exact   the whole command, character for character
  prefix  the command starts with these words (`git status` allows
          `git status -s`). An unquoted `;` `&` `|` `<` `>` backtick, `$(`,
          or newline never matches a prefix rule, because
          `git status; rm -rf ~` would otherwise ride along. Those same
          characters inside quotes are argument text
          (`git commit -m "fix > docs"`). Double quotes still expand
          `$(` and backticks, and an unclosed quote does not match.

A rule saved from an answer is tied to that conversation's workspace folder,
spelled exactly as stored (no symlink or `..` folding, so a rule can never
apply in a different directory). A rule with no folder applies in any.
Commands that contain a credential cannot be saved.
"""

import re
import shlex
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.permission_rule import PermissionRule
from app.db.transactions import begin_write
from app.services import decision_log, safety_floor

BUCKET = "local_command"
EXACT = "exact"
PREFIX = "prefix"
MATCH_KINDS = (EXACT, PREFIX)
MAX_COMMAND_CHARS = 1000
MAX_WORKSPACE_CHARS = 4096
MAX_RULES = 200
BASH_TOOL = "Bash"
# Tools whose `command` is a shell command a rule can match.
COMMAND_TOOLS = (BASH_TOOL, "mcp__computer__exec")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InvalidRule(ValueError):
    pass


def _has_shell_operators(command: str) -> bool:
    """True when a prefix match could run more than the saved words."""
    i = 0
    n = len(command)
    quote: str | None = None
    while i < n:
        char = command[i]
        if quote == "'":
            if char == "'":
                quote = None
            i += 1
            continue
        if char == "\\":
            i += 2
            continue
        if quote == '"':
            if char == '"':
                quote = None
            elif char == "`" or command.startswith("$(", i):
                return True
            i += 1
            continue
        if char in "'\"":
            quote = char
            i += 1
            continue
        if char in ";|&<>\n\r" or char == "`" or command.startswith("$(", i):
            return True
        i += 1
    return quote is not None


def _words(command: str) -> list[str] | None:
    try:
        return shlex.split(command)
    except ValueError:
        return None


def split_scope(stored: str) -> tuple[str, str | None]:
    """`exact@/work` -> (`exact`, `/work`); `exact` -> (`exact`, None). A match
    kind never contains `@`, so the first one splits it."""
    kind, at, workspace = stored.partition("@")
    return kind, (workspace if at else None)


def command_matches(rule_kind: str, rule_command: str, command: str) -> bool:
    command = command.strip()
    if rule_kind == EXACT:
        return command == rule_command.strip()
    if rule_kind == PREFIX:
        if _has_shell_operators(command):
            return False
        rule_words, words = _words(rule_command), _words(command)
        if not rule_words or words is None:
            return False
        return words[: len(rule_words)] == rule_words
    return False


def validate_workspace(workspace: str | None) -> str | None:
    if workspace is None:
        return None
    if (
        not workspace
        or len(workspace) > MAX_WORKSPACE_CHARS
        or _CONTROL.search(workspace)
        or "@" in workspace.split("/", 1)[0]
        or not (workspace.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", workspace))
        or ".." in re.split(r"[\\/]", workspace)
    ):
        raise InvalidRule("workspace must be an absolute path without '..' segments")
    return workspace


def validate_rule(kind: str, command: str, workspace: str | None = None) -> str:
    command = command.strip()
    if kind not in MATCH_KINDS:
        raise InvalidRule("match must be 'exact' or 'prefix'")
    if not command or len(command) > MAX_COMMAND_CHARS or _CONTROL.search(command):
        raise InvalidRule(f"command must be 1 to {MAX_COMMAND_CHARS} characters without control characters")
    if decision_log.redact(command) != command:
        raise InvalidRule("commands containing credentials cannot be saved; use an environment variable")
    if kind == PREFIX:
        if _has_shell_operators(command):
            raise InvalidRule("a prefix rule cannot contain shell operators")
        if not _words(command):
            raise InvalidRule("a prefix rule must be a valid command")
    if not safety_floor.clears_floor(command):
        raise InvalidRule("this command is on the safety floor and is always asked about")
    validate_workspace(workspace)
    return command


async def list_rules(db: AsyncSession, owner_id: str) -> list[PermissionRule]:
    rows = await db.execute(
        select(PermissionRule)
        .where(PermissionRule.user_id == owner_id, PermissionRule.bucket == BUCKET)
        .order_by(PermissionRule.id)
    )
    return list(rows.scalars())


async def add_rule(
    db: AsyncSession, owner_id: str, kind: str, command: str, workspace: str | None = None
) -> PermissionRule:
    """Add a rule, or return the identical one already saved."""
    command = validate_rule(kind, command, workspace)
    stored_kind = kind if workspace is None else f"{kind}@{workspace}"
    # Check-then-insert, often right after a read on the same session (the
    # thread's folder) while a woken turn writes from another connection.
    await begin_write(db)
    existing = await list_rules(db, owner_id)
    for rule in existing:
        if rule.connector == stored_kind and rule.tool == command:
            return rule
    if len(existing) >= MAX_RULES:
        raise InvalidRule(f"at most {MAX_RULES} commands can be saved")
    rule = PermissionRule(
        user_id=owner_id, bucket=BUCKET, connector=stored_kind, tool=command, decision="allow"
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


def rule_label(rule: PermissionRule) -> str:
    """How the decision log names a rule: `exact@/work: git status`."""
    return f"{rule.connector}: {rule.tool}"


def applies_in(rule: PermissionRule, workspace: str | None) -> bool:
    """A rule with no folder applies everywhere; one with a folder only there."""
    _kind, scope = split_scope(rule.connector or "")
    return scope is None or scope == workspace


async def find_rule(
    db: AsyncSession, owner_id: str, kind: str, command: str, workspace: str | None
) -> PermissionRule | None:
    """The saved rule with exactly this match kind, command and folder."""
    stored_kind = kind if workspace is None else f"{kind}@{workspace}"
    for rule in await list_rules(db, owner_id):
        if rule.connector == stored_kind and rule.tool == command.strip():
            return rule
    return None


async def delete_rule(db: AsyncSession, owner_id: str, rule_id: int) -> PermissionRule | None:
    """Delete one rule. Returns what was deleted, or None if there was none."""
    await begin_write(db)
    rule = (
        await db.execute(
            select(PermissionRule).where(
                PermissionRule.id == rule_id,
                PermissionRule.user_id == owner_id,
                PermissionRule.bucket == BUCKET,
            )
        )
    ).scalar_one_or_none()
    if rule is None:
        return None
    await db.execute(delete(PermissionRule).where(PermissionRule.id == rule.id))
    await db.commit()
    return rule


async def matching_rule(
    db: AsyncSession,
    owner_id: str,
    tool: str,
    tool_input: dict,
    workspace: str | None = None,
) -> PermissionRule | None:
    """The saved rule that allows this call in `workspace`, if any. Only a Bash
    call (on the host or in a computer) whose `command` is a string can
    match, and never one on the floor."""
    if tool not in COMMAND_TOOLS:
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    for rule in await list_rules(db, owner_id):
        if not rule.connector or not rule.tool:
            continue
        kind, scope = split_scope(rule.connector)
        if scope is not None and scope != workspace:
            continue
        if command_matches(kind, rule.tool, command):
            return rule
    return None


async def remembered_rule(
    db: AsyncSession, owner_id: str, tool: str, tool_input: dict, workspace: str | None
) -> tuple[PermissionRule | None, str | None]:
    """After an Allow with `remember`: the rule it saved, or why none was saved
    (the call was then allowed this once only)."""
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool not in COMMAND_TOOLS or not isinstance(command, str):
        return None, "only shell commands can be always allowed"
    if not workspace:
        return None, "this conversation has no folder to tie the rule to"
    workspace = str(Path(workspace))  # spelled as the runtime saves it
    rule = await find_rule(db, owner_id, EXACT, command, workspace)
    if rule is not None:
        return rule, None
    try:
        validate_rule(EXACT, command, workspace)
    except InvalidRule as exc:
        return None, str(exc)
    return None, "the rule could not be saved"
