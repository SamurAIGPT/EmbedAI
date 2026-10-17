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
