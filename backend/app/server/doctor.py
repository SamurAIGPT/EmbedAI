"""`python -m app.server doctor`: is this machine ready to run the server?

Run it with the same environment the server gets (the service's settings file, or
`set -a; . /etc/open-dots/open-dots.env; set +a`). It reads and probes; it never
changes anything: no folder is created, no database is touched, no engine runs a
turn. Each check says what it found and, when something is wrong, what to do.

    python -m app.server doctor [--json] [--check-url https://dots.example.com]

Exit code 0 when nothing is wrong (warnings are advice), 1 when something must be fixed.
`--check-url` also visits the public address, to catch what only shows from outside:
a certificate problem, a wrong name, a proxy that does not reach the server.
"""

import argparse
import asyncio
import json
import os
import socket
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
EXIT_PROBLEM = 1
SIGN_IN_ADVICE = {
    "claude": "Sign in as the account that runs the server: `claude auth login` (it prints a link; open it in any browser and paste the code back).",
    "codex": "Sign in as the account that runs the server: `codex login --device-auth` (it shows a code to enter on the provider's page).",
}


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok | warn | fail | info
    detail: str
    advice: str = ""


def _problem(name: str, detail: str, advice: str = "") -> Check:
    return Check(name, FAIL, detail, advice)


# -- settings and the server's own options ------------------------------------------------

def check_settings() -> tuple[Check, object | None]:
    """The settings must load before anything else can be judged."""
    try:
        from app.core.config import settings
    except Exception as exc:  # noqa: BLE001 - a bad setting is the finding
        errors = getattr(exc, "errors", None)
        if callable(errors):
            # Names and messages only: never the value, which may be a secret.
            lines = [f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in errors()]
            detail = "; ".join(lines) or str(type(exc).__name__)
        else:
            detail = f"{type(exc).__name__}: {exc}"
        return _problem("Settings", f"The settings do not load ({detail}).",
                        "Set the missing or invalid values in the server's settings file; TOKEN_ENCRYPTION_KEYS is required."), None
    return Check("Settings", OK, "The settings load."), settings


def check_options(environ: Mapping[str, str]) -> tuple[Check, object | None]:
    from app.server.entry import ServerConfigError, read_options

    try:
        options = read_options([], environ)
    except ServerConfigError as exc:
        return _problem("Server options", str(exc)), None
    return Check("Server options", OK, f"Listens on {options.host}:{options.port}, pages from {options.ui_dir}."), options


def check_pages(ui_dir: Path | None) -> Check:
    if ui_dir is None:
        return _problem("Pages", "No pages folder is set.", "Set OPEN_DOTS_UI_DIR to the folder made by `node scripts/build-ui.js`.")
    needed = ["index.html", "login/index.html", "pair/index.html", "chat/index.html", "settings/index.html"]
    missing = [page for page in needed if not (ui_dir / page).is_file()]
    if missing:
        return _problem("Pages", f"{ui_dir} is missing {', '.join(missing)}.",
                        "Build the pages again: `node scripts/build-ui.js`, then point OPEN_DOTS_UI_DIR at frontend/out.")
    return Check("Pages", OK, f"All {len(needed)} pages are in {ui_dir}.")


def check_port(options) -> Check:
    probe = socket.socket(socket.AF_INET6 if ":" in options.host.strip("[]") else socket.AF_INET)
    try:
        probe.settimeout(1)
        in_use = probe.connect_ex((options.host.strip("[]"), options.port)) == 0
    except OSError:
        in_use = False
    finally:
        probe.close()
    if in_use:
        return Check("Port", INFO, f"Something already answers on {options.host}:{options.port}.",
                     "That is expected if the server is running now; otherwise choose another OPEN_DOTS_PORT.")
    return Check("Port", OK, f"{options.host}:{options.port} is free.")


def check_public_url(settings) -> Check:
    origin = settings.PUBLIC_ORIGIN
    if not origin:
        return Check("Public address", WARN, "OPEN_DOTS_PUBLIC_URL is not set.",
                     "Set it to the address people use, for example https://dots.example.com. Without it the public name must be listed in TRUSTED_HOSTS, and pairing links are not printed.")
    if origin.startswith("http://"):
        return Check("Public address", WARN, f"{origin} is not HTTPS.",
                     "The sign-in cookie is only marked Secure over HTTPS, and a passphrase would travel in clear.")
    return Check("Public address", OK, f"{origin} (its host name is trusted).")


def check_security(settings) -> list[Check]:
    checks = []
    if settings.LOOPBACK_TRUST != "off":
        checks.append(Check("Loopback trust", FAIL, f"LOOPBACK_TRUST is {settings.LOOPBACK_TRUST!r}: programs on this machine act as the owner without signing in.",
                            "Set LOOPBACK_TRUST=off on a server."))
    else:
        checks.append(Check("Loopback trust", OK, "Off: every visitor signs in."))
    from app.core import app_settings

    if settings.LOCAL_EXECUTION_ENABLED:  # a start-up setting, not an editable one
        checks.append(Check("Local execution", OK, "On: agents can run on this machine."))
    else:
        checks.append(Check("Local execution", FAIL, "LOCAL_EXECUTION_ENABLED is off: no agent can run, so conversations will not start.",
                            "Set LOCAL_EXECUTION_ENABLED=true in the settings file."))
    if app_settings.get("LOCAL_TOOLS_ENABLED"):
        checks.append(Check("Tools", INFO, "On: agents can edit files and run commands, each asking your approval unless you saved a rule.", ""))
    else:
        checks.append(Check("Tools", INFO, "Off: agents can chat but cannot edit files or run commands.",
                            "Set LOCAL_TOOLS_ENABLED=true to let them (every command still asks for approval)."))
    return checks


# -- database -------------------------------------------------------------------------------

def check_database(settings) -> tuple[Check, str | None]:
    from app.server import database

    try:
        state = database.schema_state()
    except database.DatabaseRefused as exc:
        return _problem("Database", str(exc)), None
    except Exception as exc:  # noqa: BLE001 - an unreachable database is the finding
        return _problem("Database", f"Could not read the database ({type(exc).__name__}: {exc}).",
                        "Check DATABASE_BACKEND, SQLITE_PATH (or the PostgreSQL settings) and that the account can read them."), None
    where = f"SQLite file {settings.SQLITE_FILE}" if settings.DATABASE_KIND == "sqlite" else "the PostgreSQL database"
    if state == "current":
        return Check("Database", OK, f"{where} is at the newest revision."), state
    advice = "Run `python -m app.server migrate`."
    if state == "new":
        return _problem("Database", f"{where} has no tables yet.", advice), state
    return _problem("Database", f"{where} is from an older version.", advice + " A SQLite file is copied aside first."), state


def check_key() -> Check:
    from app.server.database import KeyMismatch
    from app.desktop.database import check_key as stored_credentials_open

    try:
        stored_credentials_open()
    except KeyMismatch as exc:
        return _problem("Encryption key", str(exc), "Use the TOKEN_ENCRYPTION_KEYS this database was made with; restore it from your backup.")
    except Exception as exc:  # noqa: BLE001
        return _problem("Encryption key", f"Could not check the key ({type(exc).__name__}).")
    return Check("Encryption key", OK, "Every stored credential opens with the key.")


def check_owner() -> Check:
    from sqlalchemy import func, select

    from app.db.engine import create_app_sync_engine
    from app.db.models.auth import OwnerAccount

    engine = create_app_sync_engine()
    try:
        with engine.connect() as connection:
            count = connection.execute(select(func.count()).select_from(OwnerAccount)).scalar_one()
    except Exception as exc:  # noqa: BLE001
        return Check("Owner passphrase", WARN, f"Could not tell whether a passphrase is set ({type(exc).__name__}).")
    finally:
        engine.dispose()
    if count:
        return Check("Owner passphrase", OK, "A passphrase is set.")
    return Check("Owner passphrase", WARN, "No passphrase is set yet, so nobody can sign in.",
                 "Run `python -m app.auth.cli set-passphrase` from this shell.")


# -- the machine ------------------------------------------------------------------------------

def _writable(path: Path) -> tuple[bool, Path]:
    """Whether `path`, or the nearest folder above it that exists, can be written to; without creating anything."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK | os.X_OK) and probe.is_dir(), probe


def check_folders(settings) -> list[Check]:
    from app.core import app_settings
    from app.services.workspaces import canonical_workspace

    wanted = {"Workspace folder": canonical_workspace(app_settings.get("LOCAL_WORKSPACE_ROOT"))}
    if settings.DATABASE_KIND == "sqlite":
        wanted["Database folder"] = settings.SQLITE_FILE.parent
    checks = []
    for name, path in wanted.items():
        ok, where = _writable(path)
        if ok:
            checks.append(Check(name, OK, f"{path} can be written." if path.exists() else f"{path} will be created under {where}."))
        else:
            checks.append(_problem(name, f"{path} cannot be written by this account (checked {where}).",
                                   "Make the folder owned by the account that runs the server, or point the setting somewhere it can write."))
    return checks


# -- engines -------------------------------------------------------------------------------------

def check_engines(describe: Callable | None = None) -> list[Check]:
    """One line per engine, and one verdict: at least one engine must be ready."""
    if describe is None:
        from app.services.thread_runtime import runtime

        def describe():
            return runtime.engines.describe(refresh=True)

    try:
        entries = asyncio.run(describe())
    except Exception as exc:  # noqa: BLE001 - a broken probe must not stop the check
        return [_problem("Engines", f"The engines could not be checked ({type(exc).__name__}: {exc}).")]
    checks = []
    for entry in entries:
        name = f"Engine {entry['label']}"
        if entry["available"]:
            version = f" {entry['version']}" if entry.get("version") else ""
            checks.append(Check(name, OK, f"Ready{version}."))
        else:
            advice = SIGN_IN_ADVICE.get(entry["id"], "") if entry.get("installed") and entry.get("signed_in") is False else ""
            checks.append(Check(name, INFO, entry.get("reason") or "Not ready.", advice))
    if any(entry["available"] for entry in entries):
        ready = [entry["label"] for entry in entries if entry["available"]]
        checks.append(Check("Engines", OK, f"Ready: {', '.join(ready)}."))
    else:
        checks.append(_problem("Engines", "No engine is ready, so agents cannot answer.",
                               "Install an engine as the account that runs the server and sign it in (see the lines above). "
                               "If it is installed but not found, put its folder on the PATH or set LOCAL_CLAUDE_CLI_PATH / LOCAL_CODEX_CLI_PATH."))
    return checks


# -- the public address, from outside ------------------------------------------------------------

def check_url(url: str, fetch: Callable | None = None) -> list[Check]:
    import httpx

    from app.core import public_url

    try:
        origin = public_url.normalize(url)
    except ValueError as exc:
        return [_problem("Address check", str(exc))]
    if not origin:
        return [_problem("Address check", "No address was given.")]
    get = fetch or (lambda path: httpx.get(origin + path, timeout=10, follow_redirects=False))
    checks = []
    try:
        health = get("/api/v1/health")
    except httpx.ConnectError as exc:
        reason = str(exc)
        advice = "Check the name points at this server, the proxy is running, and ports 80 and 443 are open."
        if "CERTIFICATE" in reason.upper() or "SSL" in reason.upper():
            advice = "The certificate is not valid for this name. Check the proxy's certificate setup."
        return [_problem("Address check", f"{origin} did not answer ({reason}).", advice)]
    except httpx.HTTPError as exc:
        return [_problem("Address check", f"{origin} did not answer properly ({type(exc).__name__}).")]
    if health.status_code == 421:
        checks.append(_problem("Address check", f"{origin} answers, but the server refuses that host name (421).",
                               "Set OPEN_DOTS_PUBLIC_URL to this address, and make the proxy pass the real Host header."))
    elif health.status_code != 200 or '"ok"' not in health.text:
        checks.append(_problem("Address check", f"{origin}/api/v1/health answered {health.status_code}, not the server's health.",
                               "The proxy may be sending the visit somewhere else; point it at the server's port."))
    else:
        checks.append(Check("Address check", OK, f"{origin} reaches the server."))
    if not origin.startswith("https://"):
        checks.append(Check("Address HTTPS", WARN, f"{origin} is not HTTPS.", "Use an HTTPS address."))
    try:
        page = get("/login/")
    except httpx.HTTPError:
        page = None
    if page is not None and page.status_code == 200:
        checks.append(Check("Sign-in page", OK, "The sign-in page loads from outside."))
    elif page is not None:
        checks.append(_problem("Sign-in page", f"{origin}/login/ answered {page.status_code}.", "Check OPEN_DOTS_UI_DIR and that the pages are built."))
    return checks


# -- running them ----------------------------------------------------------------------------------

def run_checks(environ: Mapping[str, str] | None = None, url: str | None = None, describe: Callable | None = None) -> list[Check]:
    env = os.environ if environ is None else environ
    checks: list[Check] = []

    from app.server.engine_path import augment_path

    added = augment_path()  # the way the server starts, so engines are found the same way

    settings_check, settings = check_settings()
    checks.append(settings_check)
    if settings is None:
        return checks

    options_check, options = check_options(env)
    checks.append(options_check)
    ui_value = env.get("OPEN_DOTS_UI_DIR", "")
    checks.append(check_pages(Path(ui_value).expanduser().resolve() if ui_value else None))
    if options is not None:
        checks.append(check_port(options))
    checks.append(check_public_url(settings))
    checks.extend(check_security(settings))

    database_check, state = check_database(settings)
    checks.append(database_check)
    if state in ("current", "behind"):
        checks.append(check_key())
    if state == "current":
        checks.append(check_owner())
    checks.extend(check_folders(settings))
    if added:
        checks.append(Check("PATH", INFO, f"Added for engines: {', '.join(added)}."))
    if state == "current":
        checks.extend(check_engines(describe))
    else:
        checks.append(Check("Engines", INFO, "Not checked: the database is not ready (engines read their saved settings from it)."))
    if url:
        checks.extend(check_url(url))
    return checks


def render(checks: Sequence[Check]) -> str:
    marks = {OK: "ok  ", WARN: "WARN", FAIL: "FAIL", INFO: "info"}
    lines = ["Open Duet server check", ""]
    for check in checks:
        lines.append(f"  {marks[check.status]}  {check.name}: {check.detail}")
        if check.advice:
            lines.append(f"        -> {check.advice}")
    problems = sum(1 for c in checks if c.status == FAIL)
    warnings = sum(1 for c in checks if c.status == WARN)
    lines += ["", f"{problems} problem(s), {warnings} warning(s)." + (" Fix the problems above first." if problems else " Ready.")]
    return "\n".join(lines)


def main(argv: Sequence[str], environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.server doctor", description="Check that this machine is ready to run the server.")
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    parser.add_argument("--check-url", metavar="URL", help="also visit this public address")
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return 2 if exc.code else 0
    checks = run_checks(environ, args.check_url)
    if args.json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        print(render(checks))
    return EXIT_PROBLEM if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
