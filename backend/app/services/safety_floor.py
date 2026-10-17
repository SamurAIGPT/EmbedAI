"""A never-list: shell commands that are not approved automatically, whatever
a saved rule says.

Only a person, looking at the exact command on a card, can allow one of these.
The floor is one more condition in front of an automatic allow; it never
overrides a card someone answers, and it cannot reach what the CLI decides for
itself (for example edits under `accept_edits`).

The rules lean towards letting a command through: words used as data (an echo,
a commit message) are not commands, and only commands the reader is sure about
are blocked. The one exception is the security invariant: a command that could
not be read to the end (an unbalanced quote, command substitution, a nested
shell) is "unreadable", never "clear", and is also held for a person.

    delete-root, delete-home, delete-system-directory   rm -r on / ~ /etc ...
    permission-change-root                              chmod/chown -R on those
    format-disk, overwrite-disk                         mkfs, diskutil eraseDisk, dd of=/dev/sda, > /dev/sda
    force-push                                          git push --force / -f / +ref
    fork-bomb                                           :(){ :|:& };:  and  function f { f|f& }; f

A command whose program is itself a variable (`$CMD args`, `bash -c "$CMD"`)
cannot be known, so it is unreadable too.
"""

import posixpath
import re
import shlex
from dataclasses import dataclass

SYSTEM_DIRECTORIES = frozenset(
    {
        "/etc", "/usr", "/bin", "/sbin", "/var", "/boot", "/home", "/lib", "/lib32", "/lib64", "/libx32", "/lib*",
        "/opt", "/root", "/srv", "/dev", "/proc", "/sys", "/System", "/Library",
        "/Applications", "/Users", "/Volumes", "/private", "/private/etc", "/private/var",
    }
)
_HOME_WORDS = frozenset({"~", "$HOME", "${HOME}"})

# Wrappers that precede the real command.
_WRAPPERS = frozenset({"sudo", "doas", "env", "nohup", "command", "time", "exec", "nice", "ionice", "builtin", "stdbuf", "timeout"})
_REDIRECTS = frozenset({">", ">>", ">&", "&>", ">|"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
# Options of a wrapper that take a value as the next word.
_VALUE_OPTIONS = frozenset({"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-n", "-c", "-s", "-k"})
_MAX_NESTING = 3

_DISK_DEVICE = re.compile(
    r"^/dev/(?:r?disk\d+(?:s\d+)*|disk/.+|sd[a-z]{1,3}\d*|hd[a-z]\d*|x?vd[a-z]{1,2}\d*|"
    r"nvme\d+(?:n\d+)?(?:p\d+)?|mmcblk\d+(?:p\d+)?|md\d+(?:p\d+)?|md/[^/]+|dm-\d+|mapper/(?!loop\d)[^/]+)$"
)
_SCRATCH_DEVICE = re.compile(r"^/dev/(?:mapper/)?(?:loop|ram|nbd|zram)\d")
# A function that pipes itself into itself in the background, then calls
# itself; written `f(){ ... }` or `function f { ... }`. The name is bounded.
_BOMB_BODY = r"\s*\{\s*\1\s*\|\s*\1\s*&\s*\}\s*;?\s*\1(?![\w:.-])"
_FORK_BOMBS = (
    re.compile(r"(?:^|[\s;&|(){}])([A-Za-z_:.][\w:.-]{0,63})\s*\(\s*\)" + _BOMB_BODY),
    re.compile(r"(?:^|[\s;&|(){}])function\s+([A-Za-z_:.][\w:.-]{0,63})\s*(?:\(\s*\))?" + _BOMB_BODY),
)
# diskutil verbs that erase a disk or volume.
_DISKUTIL_ERASE = frozenset({"erasedisk", "erasevolume", "reformat", "zerodisk", "randomdisk", "secureerase"})
# Substitution that hides what will run. Plain `$VAR` words are not read as
# anything (a word holding one is unknown, so it matches no rule).
_HIDDEN = re.compile(r"\$\(|`|<\(|>\(|\$'")

_LABELS = {
    "delete-root": "recursive delete of the filesystem root",
    "delete-home": "recursive delete of the home directory",
    "delete-system-directory": "recursive delete of a system directory",
    "permission-change-root": "recursive permission change of the root, home or a system directory",
    "format-disk": "formatting a disk",
    "overwrite-disk": "overwriting a disk device",
    "force-push": "a force push",
    "fork-bomb": "a fork bomb",
}


@dataclass(frozen=True)
class FloorVerdict:
    rule: str
    label: str  # words a person reads; never the command, which may hold a secret


@dataclass(frozen=True)
class FloorReading:
    readable: bool
    verdict: FloorVerdict | None = None

    @property
    def clear(self) -> bool:
        """True only for a command that was read and matched nothing."""
        return self.readable and self.verdict is None


def _verdict(rule: str) -> FloorVerdict:
    return FloorVerdict(rule, _LABELS[rule])


def _tokens(command: str) -> list[str] | None:
    """Words and operators, quotes removed. None when it cannot be read."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return None


def _split(tokens: list[str]) -> list[list[str]]:
    commands: list[list[str]] = [[]]
    for token in tokens:
        if token and all(ch in ";&|()" for ch in token):
            commands.append([])
        else:
            commands[-1].append(token)
    return [c for c in commands if c]


def _redirect_targets(words: list[str]) -> tuple[list[str], list[str]]:
    """The command's own words, and what its output redirections write to."""
    plain: list[str] = []
    targets: list[str] = []
    mode = ""  # "write" or "read": the next word is a redirection operand
    for word in words:
        if mode:
            if mode == "write":
                targets.append(word)
            mode = ""
        elif word in _REDIRECTS or re.fullmatch(r"\d*>>?", word):
            mode = "write"
        elif re.match(r"^\d*(?:>>?|&>)[^>&|]", word):
            targets.append(re.sub(r"^\d*(?:>>?|&>)", "", word))
        elif word in ("<", "<<", "<<<") or re.fullmatch(r"\d*<", word):
            mode = "read"
        else:
            plain.append(word)
    return plain, targets


def _strip_wrappers(words: list[str]) -> list[str]:
    """Drop `sudo`, `env X=1`, `nohup`, `timeout 5` ... in front of the command."""
    words = list(words)
    while words:
        head = words[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head):
            words.pop(0)
        elif posixpath.basename(head) in _WRAPPERS:
            wrapper = posixpath.basename(words.pop(0))
            while words and words[0].startswith("-") and words[0] != "--":
                option = words.pop(0)
                if option in _VALUE_OPTIONS and words:
                    words.pop(0)
            if words and words[0] == "--":
                words.pop(0)
            if wrapper == "timeout" and words and re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", words[0]):
                words.pop(0)
        else:
            break
    return words


_HOME_MARK = "/\ue000home"


def _dangerous_target(path: str) -> str | None:
    """The rule a recursive delete or permission change of `path` breaks."""
    if path.endswith(("/*", "/.*")):
        path = path[:-2] or "/"
    for home in ("~", "$HOME", "${HOME}"):
        if path == home or path.startswith(home + "/"):
            path = _HOME_MARK + path[len(home):]
            break
    if "$" in path or "`" in path or not path.startswith("/"):
        return None
    normal = posixpath.normpath("/" + path.lstrip("/"))
    if normal == _HOME_MARK:
        return "delete-home"
    if normal == "/":
        return "delete-root"
    if normal in SYSTEM_DIRECTORIES:
        return "delete-system-directory"
    return None


def _flags(words: list[str]) -> set[str]:
    flags: set[str] = set()
    for word in words:
        if word == "--":
            break
        if word.startswith("--"):
            flags.add(word.split("=", 1)[0])
        elif word.startswith("-") and len(word) > 1:
            flags.update(word[1:])
    return flags


def _check_command(words: list[str], targets: list[str]) -> FloorVerdict | None:
    for target in targets:
        text = target.replace("//", "/")
        if text and _DISK_DEVICE.match(text) and not _SCRATCH_DEVICE.match(text):
            return _verdict("overwrite-disk")
    words = _strip_wrappers(words)
    if not words:
        return None
    name, args = posixpath.basename(words[0]), words[1:]
    operands = [a for a in args if not a.startswith("-") or a == "-"]
    flags = _flags(args)

    if name == "rm":
        if flags & {"r", "R", "--recursive"}:
            for operand in operands:
                rule = _dangerous_target(operand)
                if rule:
                    return _verdict(rule)
        if "--no-preserve-root" in args and any(
            o.startswith("/") and posixpath.normpath("/" + o.lstrip("/")) == "/" for o in operands
        ):
            return _verdict("delete-root")
    elif name in ("chmod", "chown", "chgrp"):
        if flags & {"R", "--recursive"}:
            for operand in operands[1:]:  # the first operand is the mode or owner
                if _dangerous_target(operand):
                    return _verdict("permission-change-root")
    elif name.startswith("mkfs") or name in ("mke2fs", "newfs", "newfs_hfs", "newfs_apfs", "newfs_msdos", "wipefs", "blkdiscard"):
        if any(
            (n := o.replace("//", "/")).startswith("/dev/") and len(n) > 5 and not _SCRATCH_DEVICE.match(n)
            for o in operands
        ):
            return _verdict("format-disk")
    elif name == "diskutil":
        verbs = [o for o in operands if o.lower() != "apfs"]
        if verbs and verbs[0].lower() in _DISKUTIL_ERASE and "$" not in args[-1]:
            return _verdict("format-disk")
    elif name in ("dd", "shred"):
        for arg in args:
            device = arg[3:] if arg.startswith("of=") else arg if name == "shred" else ""
            device = device.replace("//", "/")
            if device and _DISK_DEVICE.match(device) and not _SCRATCH_DEVICE.match(device):
                return _verdict("overwrite-disk")
    elif name == "git":
        git_args = [a for a in args if a != "--no-pager"]
        # `git -C dir push ...`: skip global options that take a value.
        index = 0
        while index < len(git_args) and git_args[index].startswith("-"):
            index += 2 if git_args[index] in ("-C", "-c", "--git-dir", "--work-tree") else 1
        if git_args[index : index + 1] == ["push"]:
            rest = git_args[index + 1 :]
            if any(
                a in ("-f", "--force", "--force-with-lease", "--force-if-includes")
                or a.startswith("--force-with-lease=")
                or (a.startswith("-") and not a.startswith("--") and "f" in a[1:])
                or (a.startswith("+") and len(a) > 1)
                or re.match(r"^[^\s:]+:\+", a)
                for a in rest
            ):
                return _verdict("force-push")
    return None


def floor_reading(command: str, _depth: int = 0) -> FloorReading:
    """Read one shell command line. A block found before something unreadable
    stands; otherwise anything unreadable is not clear."""
    if any(bomb.search(command) for bomb in _FORK_BOMBS):
        return FloorReading(True, _verdict("fork-bomb"))
    tokens = _tokens(command)
    if tokens is None:
        return FloorReading(False)
    readable = not _HIDDEN.search(command)
    for words in _split(tokens):
        plain, targets = _redirect_targets(words)
        found = _check_command(plain, targets)
        if found:
            return FloorReading(True, found)
        inner = _strip_wrappers(plain)
        if not inner:
            continue
        name, args = posixpath.basename(inner[0]), inner[1:]
        if "$" in inner[0]:
            readable = False  # the program is a variable: what runs is unknown
        elif name == "eval":
            # Its words are a script. One holding a variable is unknown.
            if not args or any("$" in a for a in args) or _depth >= _MAX_NESTING:
                readable = False
                continue
            nested = floor_reading(" ".join(args), _depth + 1)
            if nested.verdict:
                return nested
            readable = readable and nested.readable
        elif name in _SHELLS and "c" in _flags(args):
            script = next((a for a in args if not a.startswith("-")), None)
            if script is None or _depth >= _MAX_NESTING:
                readable = False
                continue
            nested = floor_reading(script, _depth + 1)
            if nested.verdict:
                return nested
            readable = readable and nested.readable
    return FloorReading(readable)


def clears_floor(command: str) -> bool:
    """False for a never-list command and for one that could not be read."""
    return floor_reading(command).clear
