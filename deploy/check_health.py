"""Wait for the dashboard's database API and the imported SA catalog."""
import json
import time
from urllib.error import URLError
from urllib.request import urlopen


def wait_for_dashboard(url='http://127.0.0.1:8765/api/status', timeout=60, interval=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=min(3, max(.01, deadline - time.monotonic()))) as response:
                data = json.load(response)
            if isinstance(data, dict) and data.get('counts', {}).get('smart_accounts', 0) > 0:
                return True
        except (OSError, URLError, ValueError, TypeError, AttributeError):
            pass
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    return False


if __name__ == '__main__':
    if not wait_for_dashboard():
        raise SystemExit('Dashboard readiness failed: local API or Smart Account catalog unavailable.')
    print('Dashboard API ready; Smart Account catalog loaded.')
