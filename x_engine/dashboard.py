import csv
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .accounts import handle
from .store import Store


EXPORT_TABLES = {"posts", "events", "following", "scans"}


def export_data(store, table, format="json", target=None):
    if table not in EXPORT_TABLES or format not in {"json", "csv"}:
        raise ValueError("Unsupported export table or format.")
    rows = store.rows(f"SELECT * FROM {table}" + (" WHERE target=?" if target else ""), (handle(target),) if target else ())
    if format == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2)
    output = io.StringIO(newline="")
    columns = [r[1] for r in store.db.execute(f"PRAGMA table_info({table})")]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        # Neutralize spreadsheet formulas in untrusted profile names and posts.
        writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v
                         for k, v in row.items()})
    return output.getvalue()


def server(directory, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8", download=None):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https://pbs.twimg.com https://abs.twimg.com; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(data)

        def allowed(self, mutation=False):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed or self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.send(403, '{"error":"Local access only."}')
                return False
            if mutation and (self.headers.get("Origin") != "http://" + host or
                             self.headers.get("Content-Type", "").split(";")[0] != "application/json"):
                self.send(403, '{"error":"Same-origin JSON requests required."}')
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            route = urlparse(self.path)
            if route.path == "/":
                self.send(200, Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
                return
            store = Store(directory)
            try:
                if route.path == "/api/status":
                    self.send(200, json.dumps(store.snapshot(), ensure_ascii=False))
                elif route.path == '/api/feed':
                    q=parse_qs(route.query)
                    data=store.feed(kind=q.get('kind',['all'])[0],target=q.get('target',[None])[0],
                        query=q.get('q',[''])[0],before=q.get('before',[None])[0])
                    self.send(200,json.dumps(data,ensure_ascii=False))
                elif route.path == "/api/export":
                    query = parse_qs(route.query)
                    table, fmt = query.get("table", ["posts"])[0], query.get("format", ["json"])[0]
                    target = query.get("target", [None])[0]
                    data = export_data(store, table, fmt, target)
                    self.send(200, data, "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8", f"{table}.{fmt}")
                else:
                    self.send(404, '{"error":"Not found."}')
            except ValueError as exc:
                self.send(400, json.dumps({"error": str(exc)}))
            except Exception:
                self.send(500, '{"error":"Could not load local data."}')
            finally:
                store.close()

        def do_POST(self):
            if not self.allowed(mutation=True):
                return
            store = Store(directory)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("Invalid request size.")
                body = json.loads(self.rfile.read(size))
                if self.path == "/api/targets":
                    result = store.add_target(body["username"], int(body.get("interval", 60)), body.get("account") or None)
                elif self.path == "/api/targets/toggle":
                    with store.db:
                        changed = store.db.execute("UPDATE targets SET enabled=1-enabled,next_run=0 WHERE username=?", (handle(body["username"]),)).rowcount
                    if not changed:
                        raise ValueError("Target not found.")
                    result = {"updated": True}
                elif self.path == '/api/targets/check':
                    with store.db:
                        changed=store.db.execute('UPDATE targets SET next_run=0 WHERE username=? AND enabled=1',(handle(body['username']),)).rowcount
                    if not changed:raise ValueError('No enabled target.')
                    result={'queued':True}
                else:
                    self.send(404, '{"error":"Not found."}')
                    return
                self.send(200, json.dumps(result))
            except (ValueError, KeyError, TypeError):
                self.send(400, '{"error":"Use a valid handle, imported account, and interval of at least 60 seconds."}')
            except Exception:
                self.send(500, '{"error":"Could not update target."}')
            finally:
                store.close()

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
