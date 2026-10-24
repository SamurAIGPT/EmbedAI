"""Verify agent settings and conversational entry in an isolated browser/profile.

Build the static UI first. Run with backend/.venv/bin/python from the repo root.
Needs desktop's Playwright dependency and an installed Chrome browser.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
spec = importlib.util.spec_from_file_location('server_chat_smoke', ROOT / 'scripts/smoke-server-chat.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    with tempfile.TemporaryDirectory(prefix='open-dots-agent-ui-') as folder:
        root = Path(folder)
        backend = module.ServerBackend(root / 'server', ROOT / 'frontend/out')
        owner = None
        try:
            backend.start()
            owner = module.Owner(backend)
            owner.setup()
            # Only scratch cookies and a scratch account cross into this browser.
            state = {'cookies': [{'name': cookie.name, 'value': cookie.value, 'url': backend.url} for cookie in owner.http.cookies.jar]}
            state_path = root / 'browser-state.json'
            state_path.write_text(json.dumps(state))
            script = ROOT / 'scripts/smoke-agent-setup.cjs'
            subprocess.run(['node', str(script), backend.url, str(state_path), str(root)], cwd=ROOT, check=True, timeout=120)
            print('PASS: create lead, choose coordinated agent, edit/demote/re-promote, unsent chat setup, phone layout')
        finally:
            if owner:
                owner.close()
            backend.stop()


if __name__ == '__main__':
    main()
