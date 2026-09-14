import json
from pathlib import Path
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from deploy.check_health import wait_for_dashboard
from deploy.copy_package import copy_package
from x_engine.smart_accounts import ensure_catalog
from x_engine.store import Store


ROOT = Path(__file__).resolve().parents[1]


def test_update_copies_catalog_and_preserves_existing_data(tmp_path, monkeypatch):
    """Reproduce an old VPS layout with code but no packaged SA catalog."""
    import x_engine.smart_accounts as smart
    install = tmp_path / 'installed'
    package = install / 'x_engine'
    package.mkdir(parents=True)
    shutil.copyfile(ROOT / 'x_engine/smart_accounts.py', package / 'smart_accounts.py')
    monkeypatch.setattr(smart, '__file__', str(package / 'smart_accounts.py'))
    store = Store(tmp_path / 'data')
    try:
        store.set_setting('existing_marker', 'preserve-me')
        with pytest.raises(FileNotFoundError):
            ensure_catalog(store)
        copy_package(ROOT, install)
        ensure_catalog(store)
        assert store.db.execute('SELECT COUNT(*) FROM smart_accounts').fetchone()[0] == 55626
        assert store.setting('existing_marker') == 'preserve-me'
        assert (package / 'dashboard.html').is_file()
        assert (package / 'smart-accounts.csv.gz').read_bytes() == (ROOT / 'x_engine/smart-accounts.csv.gz').read_bytes()
        assert not (install / 'data').exists()
    finally:
        store.close()


def test_missing_asset_fails_before_replacing_installed_files(tmp_path):
    source, installed = tmp_path / 'checkout', tmp_path / 'installed'
    (source / 'x_engine').mkdir(parents=True)
    (installed / 'x_engine').mkdir(parents=True)
    (source / 'x_engine/cli.py').write_text('new version')
    (installed / 'x_engine/cli.py').write_text('working version')
    (source / 'pyproject.toml').write_text('[tool.setuptools.package-data]\nx_engine = ["missing.csv.gz"]\n')
    with pytest.raises(ValueError, match='missing.csv.gz'):
        copy_package(source, installed)
    assert (installed / 'x_engine/cli.py').read_text() == 'working version'


def test_dashboard_cli_does_not_require_catalog_import(tmp_path, monkeypatch):
    from x_engine import cli
    import x_engine.smart_accounts as smart
    monkeypatch.setattr(smart, '__file__', str(tmp_path / 'missing' / 'smart_accounts.py'))
    monkeypatch.setattr('sys.argv', ['x-engine', '--data-dir', str(tmp_path / 'data'), 'dashboard'])
    started = []
    class Server:
        server_port = 8765
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def serve_forever(self):
            started.append(True)
    monkeypatch.setattr(cli, 'server', lambda *args: Server())
    cli.main()
    assert started == [True]


def test_readiness_waits_for_catalog_and_rejects_failed_api():
    responses = [(200, {'counts': {'smart_accounts': 0}}),
                 (200, {'counts': {'smart_accounts': 55626}})]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            status, data = responses.pop(0) if len(responses) > 1 else responses[0]
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/api/status'
    try:
        assert wait_for_dashboard(url, timeout=2, interval=.01)
        assert len(responses) == 1
        responses[:] = [(500, {'error': 'unavailable'})]
        assert not wait_for_dashboard(url, timeout=.1, interval=.01)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert not wait_for_dashboard(url, timeout=.1, interval=.01)
