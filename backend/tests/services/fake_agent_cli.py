"""Synthetic vendor CLI wrapper for bounded discovery and ACP turn tests."""
import json
import os
from pathlib import Path
import runpy
import sys

base = Path(os.environ["FAKE_ACP_DIR"])
plan = json.loads((base / "plan.json").read_text())
args = sys.argv[1:]
if args == ["--version"]:
    print("fake-cli 1.2.3")
elif args == ["status"]:
    print(plan.get("cli_status", "Logged in"))
elif args == ["models"]:
    print(plan.get("cli_models", ""))
else:
    runpy.run_path(str(Path(__file__).with_name("fake_acp_agent.py")), run_name="__main__")
