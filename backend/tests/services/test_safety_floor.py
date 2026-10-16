import pytest

from app.services.safety_floor import clears_floor, floor_reading


def rule_of(command):
    reading = floor_reading(command)
    assert reading.readable, command
    return reading.verdict.rule if reading.verdict else None


@pytest.mark.parametrize(
    "command,rule",
    [
        ("rm -rf /", "delete-root"),
        ("rm -rf /*", "delete-root"),
        ("rm -Rf //", "delete-root"),
        ("rm -rf /usr/../", "delete-root"),
        ("rm --recursive --force /", "delete-root"),
        ("sudo rm -fr ~", "delete-home"),
        ("rm -rf $HOME/", "delete-home"),
        ("rm -rf ${HOME}/*", "delete-home"),
        ("rm -r ~/.", "delete-home"),
        ("rm -r /etc", "delete-system-directory"),
        ("rm -rf /usr/", "delete-system-directory"),
        ("rm -rf /Users", "delete-system-directory"),
        ("timeout 5 rm -rf /", "delete-root"),
        ("env FOO=1 nohup rm -rf /", "delete-root"),
        ("ls && rm -rf /", "delete-root"),
        ("ls; rm -rf /etc", "delete-system-directory"),
        ("bash -c 'rm -rf /'", "delete-root"),
        ("sh -c \"cd x && rm -rf ~\"", "delete-home"),
        ("chmod -R 777 /", "permission-change-root"),
        ("chown -R me ~", "permission-change-root"),
        ("mkfs.ext4 /dev/sda1", "format-disk"),
        ("sudo mkfs -t ext4 /dev/nvme0n1p2", "format-disk"),
        ("wipefs -a /dev/disk2", "format-disk"),
        ("dd if=/dev/zero of=/dev/sda bs=1M", "overwrite-disk"),
        ("echo x > /dev/sda", "overwrite-disk"),
        ("cat img >/dev/nvme0n1", "overwrite-disk"),
        ("shred -n 1 /dev/sdb", "overwrite-disk"),
        ("git push --force", "force-push"),
        ("git push -f origin main", "force-push"),
        ("git push origin main --force-with-lease", "force-push"),
        ("git push origin +main", "force-push"),
        ("git push origin HEAD:+main", "force-push"),
        ("git -C repo push -fu origin x", "force-push"),
        (":(){ :|:& };:", "fork-bomb"),
    ],
)
def test_never_list_commands_are_blocked(command, rule):
    assert rule_of(command) == rule
    assert not clears_floor(command)


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "rm -rf node_modules",
        "rm -rf ./build /tmp/x",
        "rm -r ~/projects/old",
        "rm file.txt",
        "rm -rf /tmp/scratch",
        "git push",
        "git push origin main",
        "git push -u origin feature",
        "git status",
        "git commit -m 'rm -rf /'",
        "echo rm -rf /",
        "echo 'git push --force'",
        "grep -r 'mkfs /dev/sda' docs",
        "chmod -R 755 ./src",
        "chown -R me ./data",
        "dd if=/dev/zero of=./disk.img bs=1M count=10",
        "mkfs.ext4 disk.img",
        "mkfs.ext4 /dev/loop3",
        "echo x > /dev/null",
        "make > build.log 2>&1",
        "cat a | sort | uniq",
    ],
)
def test_ordinary_commands_clear_the_floor(command):
    assert rule_of(command) is None
    assert clears_floor(command)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf 'unterminated",
        "echo $(whoami)",
        "echo `id`",
        "diff <(ls a) <(ls b)",
        "eval \"$CMD\"",
        "bash -c",
        "bash -c 'echo $(id)'",
    ],
)
def test_a_command_that_cannot_be_read_is_not_clear(command):
    reading = floor_reading(command)
    assert not reading.readable and not reading.clear


def test_a_block_found_before_the_unreadable_part_stands():
    reading = floor_reading("rm -rf / ; echo $(id)")
    assert reading.verdict and reading.verdict.rule == "delete-root"


def test_a_verdict_label_never_holds_the_command():
    reading = floor_reading("rm -rf / # token=sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
    assert "sk-ant" not in reading.verdict.label and "token" not in reading.verdict.label


def test_a_nested_shell_is_read_to_a_limit():
    deep = "echo hi"
    for _ in range(5):
        deep = "sh -c " + __import__("shlex").quote(deep)
    assert not floor_reading(deep).clear
