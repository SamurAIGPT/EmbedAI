"""Development bootstrap tests; Docker and application processes are simulated.

Run from the repository root:
    uv run --project backend python -m unittest discover -s scripts -p 'test_*.py'
"""

import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet, MultiFernet
from dotenv import dotenv_values, set_key


SCRIPTS = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("dev_env", SCRIPTS / "dev-env.py")
dev_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev_env)


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="open-dots-env-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".env"
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_generate_persistent_secrets_and_preserve_other_settings(self):
        self.path.write_text(
            "# Keep this comment\nBOAT_API_KEY=\nTOKEN_ENCRYPTION_KEYS=\n"
            "HOOK_TOKEN=your-secret-token-here\nPOSTGRES_PORT=15432\n"
        )
        values = dev_env.prepare_environment(self.path)
        cipher = Fernet(values["TOKEN_ENCRYPTION_KEYS"].encode())
        encrypted = cipher.encrypt(b"stored credential")
        saved = self.path.read_text()
        reloaded = dev_env.prepare_environment(self.path)
        self.assertEqual(self.path.read_text(), saved)
        self.assertEqual(values, reloaded)
        self.assertEqual(
            Fernet(reloaded["TOKEN_ENCRYPTION_KEYS"].encode()).decrypt(encrypted),
            b"stored credential",
        )
        self.assertTrue(reloaded["HOOK_TOKEN"])
        self.assertNotEqual(reloaded["HOOK_TOKEN"], "your-secret-token-here")
        self.assertIn("# Keep this comment", saved)
        self.assertEqual(reloaded["POSTGRES_PORT"], "15432")
        self.assertEqual(reloaded["BOAT_API_KEY"], "")

    def test_preserve_existing_rotation_keys_and_hook(self):
        old_key, new_key = Fernet.generate_key(), Fernet.generate_key()
        old_token = Fernet(old_key).encrypt(b"existing credential")
        self.path.write_text(
            f"TOKEN_ENCRYPTION_KEYS='{new_key.decode()}, {old_key.decode()}'\n"
            "HOOK_TOKEN='existing hook token'\n"
        )
        before = self.path.read_bytes()
        values = dev_env.prepare_environment(self.path)
        cipher = MultiFernet(
            [Fernet(key.strip().encode()) for key in values["TOKEN_ENCRYPTION_KEYS"].split(",")]
        )
        self.assertEqual(cipher.decrypt(old_token), b"existing credential")
        self.assertEqual(values["HOOK_TOKEN"], "existing hook token")
        self.assertEqual(self.path.read_bytes(), before)

    def test_inherited_secrets_take_precedence(self):
        self.path.write_text("TOKEN_ENCRYPTION_KEYS=\nHOOK_TOKEN=\nBOAT_API_KEY=\n")
        before = self.path.read_bytes()
        inherited = {
            "TOKEN_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
            "HOOK_TOKEN": "inherited-hook",
            "BOAT_API_KEY": "inherited-boat",
        }
        with patch.dict(os.environ, inherited):
            values = dev_env.prepare_environment(self.path)
        self.assertEqual({name: values[name] for name in inherited}, inherited)
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_environment_values_do_not_rotate_persisted_secrets(self):
        key = Fernet.generate_key().decode()
        self.path.write_text(
            f"TOKEN_ENCRYPTION_KEYS={key}\nHOOK_TOKEN=saved-hook\n"
        )
        before = self.path.read_bytes()
        with patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEYS": "", "HOOK_TOKEN": ""}):
            with self.assertRaisesRegex(ValueError, "Unset the shell variable"):
                dev_env.prepare_environment(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_hook_environment_override_does_not_replace_saved_token(self):
        key = Fernet.generate_key().decode()
        self.path.write_text(
            f"TOKEN_ENCRYPTION_KEYS={key}\nHOOK_TOKEN=saved-hook\n"
        )
        before = self.path.read_bytes()
        with patch.dict(os.environ, {"HOOK_TOKEN": ""}):
            values = dev_env.prepare_environment(self.path)
        self.assertEqual(values["HOOK_TOKEN"], "")
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_existing_encryption_key_is_never_replaced(self):
        for invalid in ("invalid-key", ", ,"):
            with self.subTest(value=invalid):
                self.path.write_text(f"TOKEN_ENCRYPTION_KEYS='{invalid}'\nHOOK_TOKEN=\n")
                before = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, "valid Fernet keys"):
                    dev_env.prepare_environment(self.path)
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_export_name_fails_without_changing_file(self):
        self.path.write_text("INVALID-NAME=value\nTOKEN_ENCRYPTION_KEYS=\n")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Invalid environment variable name"):
            dev_env.prepare_environment(self.path)
        self.assertEqual(self.path.read_bytes(), before)


class DevelopmentScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="open-dots-dev-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("scripts", "backend", "frontend", "bin", "caller"):
            (self.root / name).mkdir()
        for name in ("dev.sh", "dev-env.py"):
            shutil.copyfile(SCRIPTS / name, self.root / "scripts" / name)
        shutil.copyfile(
            SCRIPTS.parent / "backend" / ".env.example",
            self.root / "backend" / ".env.example",
        )
        (self.root / "frontend" / ".env.example").write_text(
            "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000\n"
        )
        self.env_file = self.root / "backend" / ".env"
        self.calls = self.root / "calls.log"
        # An isolated PATH also lets us verify missing prerequisite tools.
        for name in ("bash", "dirname", "seq", "cp", "tr"):
            (self.root / "bin" / name).symlink_to(shutil.which(name))
        self.env = os.environ.copy()
        for key in (
            *dotenv_values(self.root / "backend" / ".env.example"),
            "BASH_ENV", "ENV", "BASH_VERSION", "SHELLOPTS", "BASHOPTS",
            "SSL_CERT_FILE", "SSL_CERT_DIR",
        ):
            self.env.pop(key, None)
        self.env.update(
            PATH=str(self.root / "bin"),
            TEST_PYTHON=sys.executable,
            TEST_SLEEP=shutil.which("sleep"),
            TEST_CALL_LOG=str(self.calls),
        )
        self.stub("docker", """#!/bin/sh
printf 'docker %s\n' "$*" >> "$TEST_CALL_LOG"
case "$*" in
  'compose version') exit "${TEST_COMPOSE_STATUS:-0}" ;;
  info) exit "${TEST_DOCKER_STATUS:-0}" ;;
  'compose ps -q postgres') printf '%s\n' "${TEST_CONTAINER-fixture-postgres}" ;;
  inspect*) printf '%s\n' "${TEST_HEALTH:-healthy}" ;;
esac
""")
        self.stub("uv", """#!/bin/sh
printf 'uv %s\n' "$*" >> "$TEST_CALL_LOG"
if [ "$*" = sync ]; then exit "${TEST_SYNC_STATUS:-0}"; fi
if [ "$1" = run ] && [ "$2" = --no-sync ]; then
  shift 3
  if [ "$2" = --check-tls ]; then exit "${TEST_TLS_STATUS:-0}"; fi
  exec "$TEST_PYTHON" "$@"
fi
if [ "$*" = 'run alembic upgrade head' ]; then exit "${TEST_MIGRATION_STATUS:-0}"; fi
if [ "$1" = run ] && { [ "$2" = fastapi ] || [ "$2" = python ]; }; then
  exec "$TEST_SLEEP" 60
fi
""")
        self.stub("npm", """#!/bin/sh
printf 'npm %s\n' "$*" >> "$TEST_CALL_LOG"
if [ "$1" = run ] && [ "$2" = dev ]; then exec "$TEST_SLEEP" 60; fi
""")
        self.stub("sleep", """#!/bin/sh
printf 'sleep %s\n' "$*" >> "$TEST_CALL_LOG"
""")

    def stub(self, name, content):
        path = self.root / "bin" / name
        path.write_text(content)
        path.chmod(0o755)

    def run_dev(self, shell="bash", *args):
        self.calls.write_text("")
        configured = dotenv_values(self.env_file) if self.env_file.exists() else {}
        boat_key = self.env.get("BOAT_API_KEY", configured.get("BOAT_API_KEY", ""))
        bot_token = self.env.get("TELEGRAM_BOT_TOKEN", configured.get("TELEGRAM_BOT_TOKEN", ""))
        expected = ["uv run fastapi dev"]
        if "--no-frontend" not in args:
            expected.append("npm run dev")
        if "--no-bot" not in args and boat_key and bot_token:
            expected.append("uv run python -m app.telegram_bot")
        process = subprocess.Popen(
            [shell if shell == "/bin/sh" else str(self.root / "bin" / shell),
             str(self.root / "scripts" / "dev.sh"), *args],
            cwd=self.root / "caller", env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        sent_interrupt = False
        deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < deadline:
            calls = self.calls.read_text()
            if not sent_interrupt and all(command in calls for command in expected):
                process.send_signal(signal.SIGINT)
                sent_interrupt = True
            time.sleep(0.01)
        if process.poll() is None:
            process.kill()
        stdout, stderr = process.communicate(timeout=5)
        result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        return result, self.calls.read_text()

    def test_first_start_from_another_directory_and_repeat_start(self):
        for shell in ("bash", "/bin/sh"):
            with self.subTest(shell=shell):
                result, calls = self.run_dev(shell)
                self.assertIn(result.returncode, (0, 130), result.stderr)
                self.assertIn("uv run fastapi dev", calls)
                self.assertIn("npm install", calls)
                self.assertIn("npm run dev", calls)
                self.assertNotIn("run python -m app.telegram_bot", calls)
                self.assertIn("Sandbox tasks require a Boat API key", result.stdout)
                self.assertEqual(self.env_file.stat().st_mode & 0o777, 0o600)
                values = dotenv_values(self.env_file)
                cipher = Fernet(values["TOKEN_ENCRYPTION_KEYS"].encode())
                token = cipher.encrypt(b"saved credential")
                saved_backend = self.env_file.read_bytes()
                frontend_env = self.root / "frontend" / ".env.local"
                frontend_env.write_text("NEXT_PUBLIC_API_BASE_URL=http://localhost:9000\n")
                (self.root / "frontend" / "node_modules").mkdir(exist_ok=True)
                result, calls = self.run_dev(shell)
                self.assertIn(result.returncode, (0, 130), result.stderr)
                self.assertEqual(self.env_file.read_bytes(), saved_backend)
                self.assertEqual(cipher.decrypt(token), b"saved credential")
                self.assertIn("9000", frontend_env.read_text())
                self.assertNotIn("npm install", calls)
                (self.root / "frontend" / "node_modules").rmdir()

    def test_configured_bot_and_skip_flags(self):
        for shell in ("bash", "/bin/sh"):
            for flags in ((), ("--no-bot",), ("--no-frontend",), ("--no-bot", "--no-frontend")):
                with self.subTest(shell=shell, flags=flags):
                    self.env_file.write_text(
                        "BOAT_API_KEY=fixture-boat\nTELEGRAM_BOT_TOKEN=fixture-bot\n"
                        "PERMISSION_HOOK_BASE_URL=http://localhost:8000\n"
                    )
                    result, calls = self.run_dev(shell, *flags)
                    self.assertIn(result.returncode, (0, 130), result.stderr)
                    self.assertEqual("run python -m app.telegram_bot" in calls, "--no-bot" not in flags)
                    self.assertEqual("npm run dev" in calls, "--no-frontend" not in flags)
                    self.assertNotIn("BOAT_API_KEY is not set", result.stdout)

    def test_inherited_boat_key_without_file_assignment(self):
        self.env_file.write_text(
            "TELEGRAM_BOT_TOKEN=\nPERMISSION_HOOK_BASE_URL=http://localhost:8000\n"
        )
        self.env["BOAT_API_KEY"] = "inherited-boat"
        self.env["PERMISSION_HOOK_BASE_URL"] = "http://localhost:8000"
        self.env["TEST_EXPECTED_BOAT"] = "inherited-boat"
        result, calls = self.run_dev()
        self.assertIn(result.returncode, (0, 130), result.stderr)
        self.assertNotIn("BOAT_API_KEY is not set", result.stdout)
        self.assertIn("uv run alembic upgrade head", calls)

    def test_dotenv_values_are_data_and_secrets_are_not_logged(self):
        marker = self.root / "unexpected-command"
        boat = f"key with spaces; $(touch {marker}) `touch {marker}` $HOME ' quote"
        self.env_file.touch()
        set_key(self.env_file, "BOAT_API_KEY", boat)
        set_key(self.env_file, "PERMISSION_HOOK_BASE_URL", "http://localhost:8000")
        self.env["TEST_EXPECTED_BOAT"] = boat
        result, calls = self.run_dev()
        self.assertIn(result.returncode, (0, 130), result.stderr)
        self.assertFalse(marker.exists())
        self.assertNotIn(boat, result.stdout + result.stderr + calls)
        for name in ("TOKEN_ENCRYPTION_KEYS", "HOOK_TOKEN"):
            self.assertNotIn(dotenv_values(self.env_file)[name], result.stdout + result.stderr + calls)

    def test_missing_tool_does_not_create_configuration(self):
        for tool in ("uv", "npm"):
            with self.subTest(tool=tool):
                path = self.root / "bin" / tool
                saved = path.read_text()
                path.unlink()
                result, calls = self.run_dev()
                self.stub(tool, saved)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Missing required tool: {tool}", result.stderr)
                self.assertFalse(self.env_file.exists())
                self.assertEqual(calls, "")

        self.env_file.write_text("DATABASE_BACKEND=postgres\n")
        path = self.root / "bin" / "docker"
        saved = path.read_text()
        path.unlink()
        result, calls = self.run_dev("bash", "--no-frontend")
        self.stub("docker", saved)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing required tool: docker", result.stderr)
        self.assertNotIn("docker compose up", calls)

    def test_backend_only_does_not_require_npm(self):
        (self.root / "bin" / "npm").unlink()
        result, calls = self.run_dev("bash", "--no-frontend")
        self.assertIn(result.returncode, (0, 130), result.stderr)
        self.assertNotIn("npm", calls)
        self.assertFalse((self.root / "frontend" / ".env.local").exists())

    def test_failure_stops_before_application_processes(self):
        for variable, value, message in (
            ("TEST_COMPOSE_STATUS", "1", "Docker Compose is unavailable"),
            ("TEST_DOCKER_STATUS", "1", "Docker is unavailable"),
            ("TEST_SYNC_STATUS", "1", ""),
            ("TEST_CONTAINER", "", "Postgres container was not created"),
            ("TEST_HEALTH", "unhealthy", "Postgres did not become healthy"),
            ("TEST_MIGRATION_STATUS", "1", ""),
        ):
            with self.subTest(variable=variable):
                self.env[variable] = value
                result, calls = self.run_dev()
                self.env.pop(variable)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn("uv run fastapi", calls)
                self.assertNotIn("npm run dev", calls)

    def test_invalid_encryption_key_stops_before_postgres(self):
        self.env_file.write_text("TOKEN_ENCRYPTION_KEYS=invalid\nHOOK_TOKEN=\n")
        before = self.env_file.read_bytes()
        result, calls = self.run_dev()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("valid Fernet keys", result.stderr)
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertNotIn("docker compose up", calls)

    def test_postgres_health_timeout_does_not_run_migrations(self):
        self.env["TEST_HEALTH"] = "starting"
        result, calls = self.run_dev()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Postgres did not become healthy in time", result.stderr)
        self.assertEqual(calls.count("docker inspect"), 30)
        self.assertNotIn("uv run alembic", calls)
        self.assertNotIn("uv run fastapi", calls)

    def test_invalid_ca_bundle_stops_before_postgres(self):
        self.env["SSL_CERT_FILE"] = str(self.root / "missing-ca.pem")
        result, calls = self.run_dev()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set SSL_CERT_FILE", result.stderr)
        self.assertNotIn("docker compose up", calls)
        self.assertNotIn("uv run fastapi", calls)

    def test_tls_preflight_runs_only_for_configured_boat_and_stops_on_failure(self):
        self.env["TEST_TLS_STATUS"] = "1"
        result, calls = self.run_dev()
        self.assertIn(result.returncode, (0, 130), result.stderr)
        self.assertNotIn("--check-tls", calls)
        set_key(self.env_file, "BOAT_API_KEY", "fixture-boat")
        result, calls = self.run_dev()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--check-tls", calls)
        self.assertNotIn("docker compose up", calls)
        self.assertNotIn("uv run fastapi", calls)

    def test_help_and_unknown_option_do_not_start_services(self):
        result, calls = self.run_dev("/bin/sh", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertEqual(calls, "")
        result, calls = self.run_dev("bash", "--unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown option", result.stderr)
        self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
