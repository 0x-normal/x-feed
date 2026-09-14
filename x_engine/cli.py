import argparse
import asyncio
import json
import sys
from pathlib import Path

from .accounts import handle, import_files, refresh_session
from .dashboard import export_data, server
from .store import Store
from .worker import run, verify_accounts, worker_lock


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be a positive integer.")
    return number


def main():
    parser = argparse.ArgumentParser(description="Track X posts and following changes locally.")
    parser.add_argument("--data-dir", default="data", help="Encrypted vault and SQLite directory (default: ./data)")
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import-accounts", help="Import six/seven-field account files without printing secrets")
    imp.add_argument("files", nargs="+")
    smart = commands.add_parser('import-smart-accounts', help='Replace the public Smart Account catalog from CSV')
    smart.add_argument('file')
    commands.add_parser("accounts", help="List account status; never displays credentials")
    verify = commands.add_parser("verify", help="Validate stored sessions; defaults to one account")
    verify.add_argument("--account", type=handle)
    verify.add_argument("--limit", type=positive, default=1)
    refresh = commands.add_parser("refresh-session", help="Replace a session with a fresh browser cookie JSON export")
    refresh.add_argument("username", type=handle)
    refresh.add_argument("file")
    target = commands.add_parser("add-target")
    target.add_argument("username", type=handle)
    target.add_argument("--account", type=handle)
    target.add_argument("--interval", type=positive, default=60, help="Seconds between scans (minimum 60)")
    pause = commands.add_parser("pause-target")
    pause.add_argument("username", type=handle)
    commands.add_parser("status")
    provider = commands.add_parser('set-provider', help='Select the client used for verification and tracking')
    provider.add_argument('name', choices=['twifork', 'rettiwt'])
    worker = commands.add_parser("run", help="Run until stopped, or run one scan with --once")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--target", type=handle)
    dash = commands.add_parser("dashboard", help="Serve the local dashboard")
    dash.add_argument("--port", type=positive, default=8765)
    exp = commands.add_parser("export")
    exp.add_argument("table", choices=["posts", "events", "following", "scans"])
    exp.add_argument("--format", choices=["json", "csv"], default="json")
    exp.add_argument("--target", type=handle)
    exp.add_argument("--output", required=True)
    args = parser.parse_args()
    store = None
    try:
        store = Store(args.data_dir)
        result = None
        if args.command == "import-accounts":
            result = import_files(store, args.files)
        elif args.command == 'import-smart-accounts':
            from .smart_accounts import import_catalog
            with worker_lock(store.directory):
                result = import_catalog(store, args.file)
        elif args.command == "accounts":
            result = store.snapshot()["accounts"]
        elif args.command == "verify":
            with worker_lock(store.directory):
                result = asyncio.run(verify_accounts(store, args.limit, args.account))
        elif args.command == "refresh-session":
            with worker_lock(store.directory):
                result = refresh_session(store, args.username, args.file)
        elif args.command == "add-target":
            result = store.add_target(args.username, args.interval, args.account)
        elif args.command == "pause-target":
            with store.db:
                count = store.db.execute("UPDATE targets SET enabled=0 WHERE username=?", (args.username,)).rowcount
            result = {"paused": bool(count), "target": args.username}
        elif args.command == "status":
            result = store.snapshot()
        elif args.command == 'set-provider':
            with worker_lock(store.directory):
                store.set_setting('provider', args.name)
            result = {'provider': args.name}
        elif args.command == "run":
            with worker_lock(store.directory):
                try:
                    success = asyncio.run(run(store, args.once, args.target))
                    if args.once and not success:
                        sys.exit(2)
                finally:
                    store.set_setting("worker_heartbeat", 0)
        elif args.command == "dashboard":
            with server(store.directory, args.port) as httpd:
                print(f"Dashboard: http://127.0.0.1:{httpd.server_port}", flush=True)
                httpd.serve_forever()
        elif args.command == "export":
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive create prevents accidental overwrite of another file.
            with output.open("x", encoding="utf-8", newline="") as stream:
                stream.write(export_data(store, args.table, args.format, args.target))
            result = {"saved": str(output.resolve())}
        if result is not None:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if args.command == "verify" and any(r["status"] != "ready" for r in result):
                sys.exit(2)
            if args.command == "import-accounts" and result["invalid_records"]:
                sys.exit(2)
    except KeyboardInterrupt:
        print("Stopped.")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"Operation failed ({type(exc).__name__}). No credentials were logged.", file=sys.stderr)
        sys.exit(1)
    finally:
        if store:
            store.close()
