import re
import shlex

# Every way a shell can start another command: `&&`, `||`, `;`, a pipe, a
# single `&` (background), a newline, a subshell or group, a command
# substitution `$(...)` or backticks. Splitting on all of them (and then
# looking for `git` anywhere in each piece) means a harmless verb can never
# hide a riskier one that follows it. Over-splitting only ever makes a
# command look riskier, which asks a person: the safe direction.
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|&\n\r(){}`]")

_READ_VERBS = {
    "status", "log", "diff", "show", "branch", "remote",
    "fetch", "ls-files", "blame", "shortlog", "describe",
}
_PUBLISH_VERBS = {"push"}
# Everything else that mutates git's own state (commit, add, checkout,
# merge, rebase, reset, stash, tag, pull, clone) is a local mutation —
# risky enough to ask about, but not yet shared anywhere.

_BUCKET_RANK = {"read": 0, "create": 1, "publish": 2}

# git's own global flags that take the value as a *separate* following
# token (e.g. "git -C /path status", "git -c user.email=x commit") rather
# than inline with "=" (e.g. "--git-dir=/path"). Without special-casing
# these, the token right after the flag — the actual subcommand — gets
# skipped as if it were the flag's value when it isn't, or the flag's
# value gets mistaken for the subcommand when it is.
_GLOBAL_FLAGS_WITH_SEPARATE_VALUE = {"-C", "-c", "--namespace"}


def _git_invocation(tokens: list[str]) -> list[str] | None:
    """The subcommand and its arguments in one invocation, skipping
    any leading global flags (and their values) before it."""
    if not tokens or tokens[0] != "git":
        return None

    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("-"):
            if token in _GLOBAL_FLAGS_WITH_SEPARATE_VALUE:
                i += 2
            else:
                i += 1
            continue
        return tokens[i:]
    return None


def _git_invocations(segment: str, depth: int = 0):
    """Every git subcommand invoked in one command segment: `git x`, but also
    `sudo git x`, `env A=1 git x`, `xargs git x`, and a quoted script such as
    `bash -c 'git x'` (searched recursively, a few levels deep)."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        # Unbalanced quotes etc.: fall back to whitespace tokens so a `git
        # push` is still seen rather than the whole segment being skipped.
        tokens = segment.split()

    for k, token in enumerate(tokens):
        if token.rsplit("/", 1)[-1] == "git":
            invocation = _git_invocation(["git", *tokens[k + 1:]])
            if invocation is not None:
                yield invocation
        elif depth < 3 and " " in token and "git" in token:
            # An argument that is itself a script (bash -c "git push").
            for inner in _SEGMENT_SPLIT_RE.split(token):
                yield from _git_invocations(inner, depth + 1)


def _is_read_only(invocation: list[str]) -> bool:
    """Branch and remote mix inspection and mutation under the same verb."""
    verb, *args = invocation
    if verb == "branch":
        listing_flags = {"--list", "-l", "--show-current", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose"}
        # A positional name creates a branch unless listing was requested.
        # Unknown flags default to mutation, including --delete/-D and --move.
        return all(arg in listing_flags or (not arg.startswith("-") and any(flag in args for flag in ("--list", "-l"))) for arg in args)
    if verb == "remote":
        if not args or all(arg in {"-v", "--verbose"} for arg in args):
            return True
        return args[0] in {"get-url", "show"}
    return verb in _READ_VERBS


def classify_bash_command(command: str) -> tuple[str, str, str] | None:
    """If `command` invokes git, return (bucket, connector, tool) describing
    the riskiest git operation found in it. Returns None for anything else,
    so the caller falls back to the coarse Bash -> "system" mapping.

    A compound command (`a && b`, `a; b`, `a | b`, `a & b`, lines, `$(b)`)
    is classified by its single riskiest verb, not its first or last one: a
    "push" anywhere makes the whole call "publish", even if something
    harmless comes before or after it.
    """
    bucket: str | None = None
    matched_verb: str | None = None

    for segment in _SEGMENT_SPLIT_RE.split(command):
        for invocation in _git_invocations(segment):
            verb = invocation[0]
            if verb in _PUBLISH_VERBS:
                candidate = "publish"
            elif _is_read_only(invocation):
                candidate = "read"
            else:
                candidate = "create"

            if bucket is None or _BUCKET_RANK[candidate] >= _BUCKET_RANK[bucket]:
                bucket = candidate
                matched_verb = verb

    if matched_verb is None:
        return None
    return bucket, "github", f"git.{matched_verb}"
