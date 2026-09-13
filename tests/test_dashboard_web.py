import base64
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

import pytest

from x_engine.accounts import Account
from x_engine.dashboard import server
from x_engine.store import Store


PUBLIC_HOST = "43.106.141.82"
PUBLIC_ORIGIN = "https://" + PUBLIC_HOST
TOKEN = "synthetic-proxy-token-" + "a" * 40


def request(port, path="/", method="GET", headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("X_FEED_PUBLIC_ORIGIN", PUBLIC_ORIGIN)
    monkeypatch.setenv("X_FEED_PROXY_TOKEN", TOKEN)
    store = Store(tmp_path / "data")
    store.add_account(Account("sample", "synthetic-password", "test@example.org", {}))
    store.close()
    httpd = server(tmp_path / "data", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_port
    httpd.shutdown()
    httpd.server_close()
    thread.join()


@pytest.mark.parametrize("path", ["/", "/api/status", "/api/feed", "/api/export"])
def test_public_data_requires_proxy_token(dashboard, path):
    assert request(dashboard, path, headers={"Host": PUBLIC_HOST})[0] == 403
    assert request(dashboard, path, headers={"Host": PUBLIC_HOST, "X-Feed-Proxy-Token": "wrong"})[0] == 403
    assert request(dashboard, path, headers={"Host": PUBLIC_HOST, "X-Feed-Proxy-Token": TOKEN})[0] == 200


def test_public_mutations_require_exact_https_origin(dashboard):
    headers = {"Host": PUBLIC_HOST, "X-Feed-Proxy-Token": TOKEN, "Content-Type": "application/json"}
    for origin in ["https://evil.test", "http://" + PUBLIC_HOST, "null", ""]:
        assert request(dashboard, "/api/targets", "POST", dict(headers, Origin=origin), '{"username":"target"}')[0] == 403
    headers["Origin"] = PUBLIC_ORIGIN
    assert request(dashboard, "/api/targets", "POST", headers, '{"username":"target"}')[0] == 200
    headers["Sec-Fetch-Site"] = "cross-site"
    assert request(dashboard, "/api/targets", "POST", headers, '{"username":"other"}')[0] == 403


def test_local_access_preserved_and_unknown_host_rejected(dashboard):
    assert request(dashboard)[0] == 200
    assert request(dashboard, headers={"Host": "evil.test", "X-Feed-Proxy-Token": TOKEN})[0] == 403


def test_media_policy_allows_x_video_cdn(dashboard):
    status, _, headers = request(dashboard)
    assert status == 200
    policy = headers['Content-Security-Policy']
    assert 'media-src https://video.twimg.com;' in policy
    assert "img-src 'self' https://pbs.twimg.com https://abs.twimg.com;" in policy


def test_api_supports_multiple_excluded_types(dashboard, tmp_path):
    store = Store(tmp_path / 'data')
    try:
        store.add_target('target')
        store.save_posts('target', [dict(id=str(i), text='sample', kind=kind,
                         url='https://x.com/i/status/'+str(i), published_at=100+i)
                         for i, kind in enumerate(['post', 'reply', 'quote', 'repost'])], True)
    finally:
        store.close()
    status, body, _ = request(dashboard, '/api/feed?exclude=reply&exclude=quote')
    assert status == 200
    assert {r['kind'] for r in json.loads(body)['items']} == {'post', 'repost'}
    assert request(dashboard, '/api/feed?exclude=invalid')[0] == 400


@pytest.mark.parametrize("origin,token", [
    ("http://example.org", TOKEN), (PUBLIC_ORIGIN + "/", TOKEN),
    ("https://user:password@example.org", TOKEN), (PUBLIC_ORIGIN, ""),
    ("", TOKEN), (PUBLIC_ORIGIN, "short"),
])
def test_incomplete_or_unsafe_public_configuration_fails_closed(tmp_path, monkeypatch, origin, token):
    monkeypatch.setenv("X_FEED_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("X_FEED_PROXY_TOKEN", token)
    with pytest.raises(ValueError):
        server(tmp_path, 0)


def test_real_caddy_authenticates_all_routes_and_preserves_csrf(dashboard, tmp_path):
    executable = os.environ.get("X_FEED_TEST_CADDY")
    if not executable:
        pytest.skip("Set X_FEED_TEST_CADDY to run the real proxy integration test.")
    password = "synthetic-website-password"
    hashed = subprocess.run([executable, "hash-password"], input=(password + "\n").encode(),
                            capture_output=True, check=True).stdout.decode().strip()
    template = Path("deploy/Caddyfile.template").read_text()
    config_file = tmp_path / "Caddyfile"
    config_file.write_text(template.replace("__HOST__", PUBLIC_HOST)
                          .replace("__PASSWORD_HASH__", hashed).replace("__PROXY_TOKEN__", TOKEN)
                          .replace("127.0.0.1:8765", f"127.0.0.1:{dashboard}"))
    subprocess.run([executable, "validate", "--config", str(config_file), "--adapter", "caddyfile"],
                   capture_output=True, text=True, check=True)
    adapted = subprocess.run([executable, "adapt", "--config", str(config_file), "--adapter", "caddyfile"],
                             capture_output=True, text=True, check=True)
    config = json.loads(adapted.stdout)
    issuer = config["apps"]["tls"]["automation"]["policies"][0]["issuers"][0]
    assert issuer["module"] == "acme" and issuer["profile"] == "shortlived"
    assert issuer["ca"] == "https://acme-v02.api.letsencrypt.org/directory"
    assert issuer["challenges"]["http"]["disabled"] is True
    assert not issuer["challenges"].get("tls-alpn", {}).get("disabled", False)
    production_server = config["apps"]["http"]["servers"]["srv0"]
    assert production_server["listen"] == [":443"]
    assert production_server["automatic_https"]["disable_redirects"] is True
    # Exercise production authentication/proxy routes on a temporary local HTTP
    # listener. Do not issue certificates or modify the machine's trust store.
    with socket.socket() as free_port:
        free_port.bind(("127.0.0.1", 0))
        port = free_port.getsockname()[1]
    config["apps"].pop("tls")
    http_server = config["apps"]["http"]["servers"]["srv0"]
    http_server["listen"] = [f"127.0.0.1:{port}"]
    http_server.pop("tls_connection_policies")
    http_server["automatic_https"] = {"disable": True}
    local_config = tmp_path / "proxy.json"
    local_config.write_text(json.dumps(config))
    environment = dict(os.environ, XDG_DATA_HOME=str(tmp_path / "caddy-data"),
                       XDG_CONFIG_HOME=str(tmp_path / "caddy-config"))
    with (tmp_path / "proxy.log").open("w") as log:
        process = subprocess.Popen([executable, "run", "--config", str(local_config)],
                                   stdout=log, stderr=log, env=environment)
        try:
            for _ in range(50):
                try:
                    request(port)
                    break
                except OSError:
                    time.sleep(0.1)
            for path in ["/", "/api/status", "/api/feed", "/api/export"]:
                assert request(port, path, headers={"Host": PUBLIC_HOST})[0] == 401
                bad = base64.b64encode(b"xfeed:wrong-password").decode()
                assert request(port, path, headers={"Host": PUBLIC_HOST, "Authorization": "Basic " + bad})[0] == 401
            assert request(port, "/api/targets", "POST", {"Host": PUBLIC_HOST}, '{}')[0] == 401
            auth = "Basic " + base64.b64encode(("xfeed:" + password).encode()).decode()
            headers = {"Host": PUBLIC_HOST, "Authorization": auth, "Content-Type": "application/json"}
            assert request(port, "/api/status", headers=headers)[0] == 200
            assert request(port, "/api/targets", "POST", dict(headers, Origin="https://evil.test"),
                           '{"username":"target"}')[0] == 403
            assert request(port, "/api/targets", "POST", dict(headers, Origin=PUBLIC_ORIGIN),
                           '{"username":"target"}')[0] == 200
        finally:
            process.terminate()
            process.wait(timeout=10)
