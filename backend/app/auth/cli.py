"""Owner administration from the server's own shell (docs/AUTH.md).

    uv run python -m app.auth.cli set-passphrase          # prompts twice
    uv run python -m app.auth.cli set-passphrase --stdin  # reads one line
    uv run python -m app.auth.cli pairing-code [--scope chat] [--label "Phone"]
    uv run python -m app.auth.cli devices
    uv run python -m app.auth.cli revoke ID

Having a shell on the server is the authority here, as it is for backend/.env.
"""

import argparse
import asyncio
import getpass
import sys

from app.auth import credentials, store


async def _run(args: argparse.Namespace) -> int:
    from app.db.session import AsyncSessionLocal, engine

    try:
        async with AsyncSessionLocal() as db:
            if args.command == "set-passphrase":
                if args.stdin:
                    passphrase = sys.stdin.readline().rstrip("\r\n")
                else:
                    passphrase = getpass.getpass("New owner passphrase: ")
                    if getpass.getpass("Repeat it: ") != passphrase:
                        print("The two entries differ; nothing changed.", file=sys.stderr)
                        return 1
                try:
                    await store.set_passphrase(db, passphrase, first_time_only=False)
                except credentials.WeakPassphrase as exc:
                    print(str(exc), file=sys.stderr)
                    return 1
                print("Owner passphrase set. Browsers signed in before must sign in again.")
            elif args.command == "pairing-code":
                try:
                    code, record = await store.create_pairing_code(db, label=args.label, scope=args.scope)
                except store.TooManyOpenCodes:
                    print("Several pairing codes are already open; wait for them to expire.", file=sys.stderr)
                    return 1
                print(f"pairing code: {credentials.format_pairing_code(code)}")
                print(f"scope:        {args.scope}")
                print(f"expires:      {record.expires_at.isoformat(timespec='seconds')} (single use)")
                print("Open /pair on the web UI's address and type it.")
            elif args.command == "devices":
                sessions = await store.list_active_sessions(db)
                if not sessions:
                    print("No signed-in browsers or paired devices.")
                for s in sessions:
                    print(f"{s.id:>5}  {s.kind:<7}  {' '.join(s.scopes):<10}  "
                          f"last seen {s.last_seen_at.isoformat(timespec='minutes')}  {s.label}")
            elif args.command == "revoke":
                if not await store.revoke_session(db, args.id):
                    print("No such active device.", file=sys.stderr)
                    return 1
                print("Revoked.")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.auth.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    setter = commands.add_parser("set-passphrase", help="set or replace the owner passphrase")
    setter.add_argument("--stdin", action="store_true", help="read the passphrase from standard input")
    pairing = commands.add_parser("pairing-code", help="open a one-time pairing code")
    pairing.add_argument("--scope", choices=("admin", "chat"), default="admin")
    pairing.add_argument("--label", default="")
    commands.add_parser("devices", help="list signed-in browsers and paired devices")
    revoke = commands.add_parser("revoke", help="revoke a browser session or device")
    revoke.add_argument("id", type=int)
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
