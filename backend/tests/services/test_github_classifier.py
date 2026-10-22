import pytest

from app.services.github_classifier import classify_bash_command


def test_non_git_command_returns_none() -> None:
    assert classify_bash_command("mkdir deny_test_dir") is None


def test_git_status_is_read() -> None:
    assert classify_bash_command("git status") == ("read", "github", "git.status")


def test_git_commit_is_create() -> None:
    assert classify_bash_command("git commit -m 'msg'") == (
        "create",
        "github",
        "git.commit",
    )


def test_git_push_is_publish() -> None:
    assert classify_bash_command("git push origin main") == (
        "publish",
        "github",
        "git.push",
    )


def test_compound_command_classified_by_riskiest_verb() -> None:
    # "push" is the riskiest verb present, even though it's not last.
    command = "git push origin main && git status"
    assert classify_bash_command(command) == ("publish", "github", "git.push")


def test_compound_command_without_push_is_create() -> None:
    command = "git add . && git commit -m 'msg'"
    assert classify_bash_command(command) == ("create", "github", "git.commit")


def test_unknown_git_verb_defaults_to_create() -> None:
    # Any git subcommand not explicitly known to be read-only is treated as
    # a mutation — safer default than assuming it's harmless.
    assert classify_bash_command("git rebase -i HEAD~3") == (
        "create",
        "github",
        "git.rebase",
    )


def test_global_flag_with_separate_value_before_subcommand() -> None:
    # Regression: a leading "-C <dir>" global flag used to get misread as
    # the subcommand itself, producing a bogus "git.-" classification.
    assert classify_bash_command("git -C /home/user/git-test status") == (
        "read",
        "github",
        "git.status",
    )


def test_repeated_dash_c_config_flags_before_subcommand() -> None:
    # Regression: exactly the pattern that broke in practice — setting a
    # per-repo git identity inline before the real subcommand.
    command = (
        "git -c user.email=test@example.com -c user.name=test-user "
        "commit -m 'test commit'"
    )
    assert classify_bash_command(command) == ("create", "github", "git.commit")


def test_inline_equals_flag_is_not_treated_as_taking_a_separate_value() -> None:
    # "--git-dir=..." embeds its value with "=", so the token right after it
    # is the real subcommand, not something to skip.
    command = "git --git-dir=/home/user/git-test/.git push"
    assert classify_bash_command(command) == ("publish", "github", "git.push")


@pytest.mark.parametrize(
    "command",
    [
        "git status\ngit push origin --force main",  # newline
        "git status\r\ngit push origin main",  # CRLF
        "git status & git push origin main",  # background operator
        "git status || git push origin main",
        "git status; git push origin main",
        "echo $(git push origin main)",  # command substitution
        "echo `git push origin main`",
        "(git status; git push origin main)",  # subshell
        "{ git status; git push origin main; }",  # group
        "git status | xargs git push",
        "sudo git push origin main",  # wrapper commands
        "env GIT_TRACE=1 git push origin main",
        "/usr/bin/git push origin main",
        "bash -c 'git status && git push origin main'",  # nested script
        'sh -c "git push origin main"',
        "git status\ngit commit -m x\ngit push",
        "git 'unterminated\ngit push origin main",  # unbalanced quotes still seen
    ],
)
def test_push_hidden_behind_another_command_is_still_publish(command: str) -> None:
    assert classify_bash_command(command) == ("publish", "github", "git.push")


def test_newline_does_not_hide_a_mutation_behind_a_read() -> None:
    assert classify_bash_command("git status\ngit commit -m x") == (
        "create",
        "github",
        "git.commit",
    )


def test_wrapped_read_only_git_stays_read() -> None:
    assert classify_bash_command("git status\ngit log --oneline") == (
        "read",
        "github",
        "git.log",
    )
    assert classify_bash_command("cat .gitignore") is None


@pytest.mark.parametrize(
    "command",
    [
        # Another command chained with a read-only git verb.
        "git status; touch x",
        "git status && touch x",
        "touch x || git status",
        "git log | sh",
        "git status\ntouch x",
        "git status & touch x",
        "cd /tmp && git status",
        # Substitution, expansion and redirection.
        "git log $(touch x)",
        "git log `touch x`",
        "echo $(git status)",
        "git show $REV",
        "git log > out.txt",
        "git diff >> out.txt",
        "git log < in.txt",
        # Wrappers whose behaviour isn't git's own.
        "sudo git status",
        "env A=1 git status",
        "bash -c 'git status'",
        "./git status",
        "git 'unterminated status",
        # Options of read verbs that run a program or write a file.
        "git -c core.pager=x log",
        "git -c alias.st=x st",
        "git -c diff.external=x diff",
        "git --config-env=core.pager=X log",
        "git --exec-path=/tmp log",
        "git --git-dir=/tmp/x status",
        "git fetch --upload-pack=x origin",
        "git fetch --upl=x origin",
        "git diff --output=out.txt",
        "git diff --outp=out.txt",
        "git log --output out.txt",
        "git diff --ext-diff",
        "git log --textconv -p",
    ],
)
def test_read_verb_with_anything_else_is_not_read(command: str) -> None:
    # Only a plain read-only git invocation is a read; anything that could
    # run another program or write a file must not ride on a "read" allow.
    classified = classify_bash_command(command)
    assert classified is None or classified[0] != "read"


@pytest.mark.parametrize(
    "command,tool",
    [
        ("git status", "git.status"),
        ("git status --short", "git.status"),
        ("git log --oneline -n 5", "git.log"),
        ("git log --format=%H -- src/app.py", "git.log"),
        ("git diff HEAD~1 -- README.md", "git.diff"),
        ("git diff --no-ext-diff --stat", "git.diff"),
        ("git show HEAD:README.md", "git.show"),
        ("git -C /repo status", "git.status"),
        ("git --no-pager log", "git.log"),
        ("git -c color.ui=false log", "git.log"),
        ("git blame README.md", "git.blame"),
        ("git ls-files", "git.ls-files"),
        ("git fetch origin", "git.fetch"),
        ("git branch --list", "git.branch"),
        ("git remote -v", "git.remote"),
        ("git status && git log --oneline", "git.log"),
        ("git status\n", "git.status"),
    ],
)
def test_plain_read_only_git_stays_read(command: str, tool: str) -> None:
    assert classify_bash_command(command) == ("read", "github", tool)


def test_mutation_after_another_command_keeps_its_bucket() -> None:
    assert classify_bash_command("touch x && git commit -m x") == ("create", "github", "git.commit")
    assert classify_bash_command("touch x; git push") == ("publish", "github", "git.push")
