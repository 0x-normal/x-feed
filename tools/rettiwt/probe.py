"""Launch one Rettiwt test using the existing encrypted account vault."""
import argparse
import json
import subprocess
import time
from pathlib import Path

from x_engine.accounts import handle
from x_engine.store import Store
from x_engine.worker import worker_lock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--account', required=True, type=handle)
    parser.add_argument('--target', default='heyfrens1', type=handle)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    node = directory / 'node_modules' / 'node-win-x64' / 'bin' / 'node.exe'
    store = Store()
    try:
        with worker_lock(store.directory):
            row = store.rows('SELECT status,reason,cooldown_until FROM accounts WHERE username=?', (args.account,))
            if not row:
                raise ValueError('Account not imported.')
            if row[0]['reason'] == 'AccountSuspended':
                raise ValueError('Selected account is known suspended.')
            if max(row[0]['cooldown_until'], float(store.setting('global_cooldown_until'))) > time.time():
                raise ValueError('An existing cooldown is active.')
            secret = store.credentials(args.account)
            result = subprocess.run([str(node), str(directory / 'probe.mjs')],
                input=json.dumps({'account': args.account, 'target': args.target, 'cookies': secret['cookies']}),
                text=True, encoding='utf-8', capture_output=True, timeout=150, cwd=directory)
            # Never forward dependency stderr; it may embed request credentials.
            try:
                report = json.loads(result.stdout)
            except (ValueError, TypeError):
                report = {'ok': False, 'reason': 'probe_output_unavailable', 'exit_code': result.returncode}
            response = report.get('response') or {}
            if response.get('httpStatus') == 429:
                until = max(time.time() + 900, response.get('rateLimitReset') or 0) + 5
                store.set_setting('global_cooldown_until', until)
                store.account_state(args.account, 'cooldown', 'rate_limited', until)
            report['account'] = args.account
            print(json.dumps(report, indent=2))
            return 0 if report.get('ok') else 2
    except subprocess.TimeoutExpired:
        print(json.dumps({'ok': False, 'reason': 'probe_timeout'}))
        return 2
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
