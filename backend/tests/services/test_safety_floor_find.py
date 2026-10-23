"""find can delete what rm cannot be pointed at: the floor reads it the same way."""

import pytest

from app.services.safety_floor import clears_floor, floor_reading


def rule_of(command):
    reading = floor_reading(command)
    assert reading.readable, command
    return reading.verdict.rule if reading.verdict else None


@pytest.mark.parametrize(
    "command,rule",
    [
        ("find / -delete", "delete-root"),
        ("find ~ -delete", "delete-home"),
        ("find $HOME -type f -delete", "delete-home"),
        ("find /etc -delete", "delete-system-directory"),
        ("sudo find /usr -name '*.so' -delete", "delete-system-directory"),
        ("find / -exec rm -rf {} +", "delete-root"),
        (r"find ~ -exec rm -r {} \;", "delete-home"),
        ("ls && find / -delete", "delete-root"),
    ],
)
def test_find_that_deletes_a_protected_tree_is_blocked(command, rule):
    assert rule_of(command) == rule
    assert not clears_floor(command)


@pytest.mark.parametrize(
    "command",
    [
        "find . -name '*.pyc' -delete",
        "find ./build -type f -delete",
        "find /tmp/scratch -delete",
        "find ~/projects/old -delete",
        "find / -name core -print",
        "find ~ -type f -exec grep -l TODO {} +",
        "find . -exec rm -rf {} +",
    ],
)
def test_find_inside_a_work_tree_or_without_deleting_clears_the_floor(command):
    assert rule_of(command) is None
    assert clears_floor(command)
