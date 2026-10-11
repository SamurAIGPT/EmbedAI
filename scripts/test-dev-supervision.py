"""Exercise the actual launcher with owned fake tools and real child processes."""
import os
from pathlib import Path
import shutil
import select
import time
import signal
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parent / "dev.sh"


class DevelopmentSupervisorTests(unittest.TestCase):
    def test_service_failures_stop_the_launcher(self):
        for failed, service_exit in (("api", 7), ("bot", 7), ("frontend", 7), ("api", 0), ("none", 143)):
            with self.subTest(service=failed, exit_code=service_exit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for folder in ("scripts", "backend", "frontend/node_modules", "bin"):
                    (root / folder).mkdir(parents=True)
                shutil.copyfile(SOURCE, root / "scripts/dev.sh")
                (root / "backend/.env").write_text(
                    "BOAT_API_KEY=owned\nTOKEN_ENCRYPTION_KEYS=owned\nTELEGRAM_BOT_TOKEN=owned\n"
                    "HOOK_TOKEN=owned\nPERMISSION_HOOK_BASE_URL=http://127.0.0.1:1\n"
                )
                tools = {
                    "docker": 'case "$1" in inspect) echo healthy;; compose) [ "$2" != ps ] || echo owned-container;; esac\n',
                    "uv": 'case "$*" in "sync"|"run alembic upgrade head") exit 0;; "run fastapi "*) kind=api;; "run python "*) kind=bot;; esac\n',
                    "npm": 'kind=frontend\n',
                }
                for name, body in tools.items():
                    if name != "docker":
                        body += '[ "$kind" != "$FAILED_SERVICE" ] || exit "$SERVICE_EXIT"\ntrap "exit 0" TERM\nwhile :; do sleep 0.1; done\n'
                    tool = root / "bin" / name
                    tool.write_text("#!/usr/bin/env bash\n" + body)
                    tool.chmod(0o755)
                env = {**os.environ, "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"], "FAILED_SERVICE": failed, "SERVICE_EXIT": str(service_exit)}
                proc = subprocess.Popen(["bash", str(root / "scripts/dev.sh")], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, start_new_session=True)
                try:
                    if failed == "none":
                        deadline, captured = time.monotonic() + 4, b""
                        while b"Everything is up" not in captured:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0 or not select.select([proc.stdout], [], [], remaining)[0]:
                                self.fail("stack never reached running state")
                            chunk = os.read(proc.stdout.fileno(), 4096)
                            if not chunk:
                                self.fail("launcher exited before the running state")
                            captured += chunk
                        proc.send_signal(signal.SIGTERM)
                    try:
                        output, _ = proc.communicate(timeout=4)
                    except subprocess.TimeoutExpired:
                        self.fail(f"launcher ignored the exited {failed} service")
                    self.assertEqual(proc.returncode, service_exit or 1, output)
                    self.assertIn("Shutting down" if failed == "none" else "exited", output)
                finally:
                    # Kill only this owned fixture's process group, including baseline orphans.
                    snapshot = subprocess.check_output(["ps", "-Ao", "pid=,command="], text=True)
                    for line in snapshot.splitlines():
                        parts = line.strip().split(None, 1)
                        if len(parts) == 2 and parts[1].startswith("bash " + str(root / "bin") + "/"):
                            try:
                                os.kill(int(parts[0]), signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                    proc.communicate()


if __name__ == "__main__":
    unittest.main()
