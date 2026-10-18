"""Git's actual ref/config changes are the oracle, in a disposable repository."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.services.github_classifier import classify_bash_command


class GitMutationClassificationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="open-dots-git-classifier-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "--quiet")
        self.git("-c", "user.name=Regression Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "--quiet", "-m", "fixture")
        self.git("branch", "topic")
        self.git("remote", "add", "origin", "https://example.invalid/initial")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True, env={"PATH": os.defpath, "HOME": str(self.root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}).stdout

    def test_branch_delete_changes_refs_and_is_a_mutation(self):
        self.git("branch", "-D", "topic")
        self.assertNotIn("refs/heads/topic", self.git("show-ref"))
        self.assertEqual(classify_bash_command("git branch -D topic"), ("create", "github", "git.branch"))

    def test_branch_creation_changes_refs_and_is_a_mutation(self):
        self.git("branch", "new-topic")
        self.assertIn("refs/heads/new-topic", self.git("show-ref"))
        self.assertEqual(classify_bash_command("git branch new-topic"), ("create", "github", "git.branch"))

    def test_branch_rename_changes_refs_and_is_a_mutation(self):
        self.git("branch", "-m", "topic", "renamed")
        self.assertIn("refs/heads/renamed", self.git("show-ref"))
        self.assertEqual(classify_bash_command("git branch -m topic renamed"), ("create", "github", "git.branch"))

    def test_remote_set_url_changes_configuration_and_is_a_mutation(self):
        self.git("remote", "set-url", "origin", "https://example.invalid/changed")
        self.assertEqual(self.git("remote", "get-url", "origin").strip(), "https://example.invalid/changed")
        self.assertEqual(classify_bash_command("git remote set-url origin https://example.invalid/changed"), ("create", "github", "git.remote"))

    def test_remote_remove_changes_configuration_and_is_a_mutation(self):
        self.git("remote", "remove", "origin")
        self.assertEqual(self.git("remote"), "")
        self.assertEqual(classify_bash_command("git remote remove origin"), ("create", "github", "git.remote"))

    def test_listing_controls_remain_read_only(self):
        for args in [("branch",), ("branch", "--list", "topic"), ("branch", "-a"), ("branch", "-l", "topic"), ("remote",), ("remote", "-v"), ("remote", "get-url", "origin")]:
            with self.subTest(args=args):
                self.git(*args)
                self.assertEqual(classify_bash_command("git " + " ".join(args)), ("read", "github", "git." + args[0]))

    def test_mutation_in_compound_command_and_global_options_is_not_read(self):
        for command in ["git -C /fixture branch -D topic", "git status && git remote remove origin", "git branch --list --delete topic"]:
            with self.subTest(command=command):
                self.assertEqual(classify_bash_command(command)[0], "create")

    def test_other_git_risk_buckets_and_wrappers_are_preserved(self):
        cases = [
            ("git status", "read", "git.status"),
            ("git -c user.name=fixture log --oneline", "read", "git.log"),
            ("git commit -m fixture", "create", "git.commit"),
            ("git rebase main", "create", "git.rebase"),
            ("git push origin main && git status", "publish", "git.push"),
            ("bash -c 'git status && git push origin main'", "publish", "git.push"),
        ]
        for command, bucket, tool in cases:
            with self.subTest(command=command):
                self.assertEqual(classify_bash_command(command), (bucket, "github", tool))
