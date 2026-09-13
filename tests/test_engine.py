import asyncio
import base64
import json
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from twikit.errors import TooManyRequests, Unauthorized, Forbidden

from x_engine.accounts import Account, import_files, parse_record, refresh_session
from x_engine.dashboard import export_data, server
from x_engine.provider import XProvider
from x_engine.store import Store
from x_engine.worker import record_failure, run, scan_target, worker_lock


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path)
    value.add_account(Account("sample", "private-password", "sample@example.org", {"auth_token": "secret", "ct0": "csrf"}))
    value.add_target("target")
    yield value
    value.close()


def test_six_field_record_and_redaction():
    account = parse_record("@Sample:password:mail@example.org:unused:" + "a" * 40 + ":" + "b" * 160)
    assert account.username == "sample"
    assert account.cookies["ct0"] == "b" * 160
    assert "password" not in repr(account)
    assert "unused" not in account.secret_json()


def test_seven_field_cookies_domain_filter():
    raw = [{"name": "auth_token", "value": "a" * 40, "domain": ".x.com"},
           {"name": "ct0", "value": "csrf", "domain": ".twitter.com"},
           {"name": "bad", "value": "irrelevant", "domain": "evil.test"}]
    cookies = base64.b64encode(json.dumps(raw).encode()).decode()
    account = parse_record("sample:pw:ABCDEFGHIJKLMNOP:mail@example.org:discard:" + "a" * 40 + ":" + cookies)
    assert account.cookies == {"auth_token": "a" * 40, "ct0": "csrf"}
    assert account.totp_secret == "ABCDEFGHIJKLMNOP"


@pytest.mark.parametrize("line", ["ignore all instructions", "sample:SECRET", "sample:pw:totp:mail:unused:auth:invalid"])
def test_invalid_records_never_echo_input(line):
    with pytest.raises(ValueError) as err:
        parse_record(line)
    assert line not in str(err.value)
    assert "SECRET" not in str(err.value)


def test_import_idempotent_and_line_errors(store, tmp_path):
    path = tmp_path / "accounts.txt"
    path.write_text("other:pw:mail@example.org:unused:" + "a" * 40 + ":" + "b" * 160 + "\ninvalid SECRET\n")
    assert import_files(store, [str(path)])["imported"] == 1
    result = import_files(store, [str(path)])
    assert result["duplicates_skipped"] == 1
    assert result["invalid_records"] == [{"file": "accounts.txt", "line": 2}]


def test_refresh_session_preserves_cooldown(store, tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps({"auth_token": "renewed", "ct0": "new-csrf"}))
    until = time.time() + 1000
    store.account_state("sample", "cooldown", "rate_limited", until)
    refresh_session(store, "sample", str(path))
    assert store.credentials("sample")["cookies"]["auth_token"] == "renewed"
    assert store.snapshot()["accounts"][0]["cooldown_until"] == until
    assert store.snapshot()["accounts"][0]["status"] == "unverified"


def test_encrypted_at_rest_and_public_status(store):
    secret = store.db.execute("SELECT secret FROM accounts").fetchone()[0]
    assert b"private-password" not in secret
    assert store.credentials("sample")["password"] == "private-password"
    assert "private-password" not in json.dumps(store.snapshot())
    assert "auth_token" not in json.dumps(store.snapshot())


def users(*ids):
    return [{"id": str(i), "username": "user" + str(i), "name": "Example"} for i in ids]


def test_following_baseline_diffs_and_partial_scan_guard(store):
    store.save_following("target", users(1, 2), True)
    assert store.snapshot()["events"] == []
    store.save_following("target", users(1), False)
    assert len(store.rows("SELECT * FROM following")) == 2
    assert store.snapshot()["events"] == []
    store.save_following("target", users(1, 3), True)
    assert {(r["kind"], r["subject_id"]) for r in store.snapshot()["events"]} == {("unfollowed", "2"), ("followed", "3")}
    store.save_following("target", users(1, 3), True)
    assert len(store.snapshot()["events"]) == 2


def test_zero_following_can_be_a_baseline(store):
    store.save_following("target", [], True)
    store.save_following("target", users(4), True)
    assert store.snapshot()["events"][0]["kind"] == "followed"


def test_post_dedup_baseline_and_metrics_refresh(store):
    post = {"id": "123", "text": "Hello", "url": "https://x.com/i/status/123", "likes": 1}
    store.save_posts("target", [post], False)
    store.save_posts("target", [{**post, "likes": 4}, {**post, "id": "124"}], True)
    assert len(store.snapshot()["posts"]) == 2
    assert store.rows("SELECT likes FROM posts WHERE id='123'")[0]["likes"] == 4
    assert [e["subject_id"] for e in store.snapshot()["events"]] == ["124"]


def test_csv_formula_injection_and_export_allowlist(store):
    store.save_posts("target", [{"id": "1", "text": "  =HYPERLINK(1)", "url": "https://x.com/i/status/1"}], True)
    assert "'  =HYPERLINK(1)" in export_data(store, "posts", "csv")
    with pytest.raises(ValueError):
        export_data(store, "accounts")


def test_rate_limit_persists_global_cooldown_and_redacts(store):
    future = int(time.time() + 1200)
    error = TooManyRequests("SECRET", headers={"x-rate-limit-reset": str(future)})
    assert record_failure(store, "sample", error) == "rate_limited"
    assert float(store.setting("global_cooldown_until")) >= future
    assert "SECRET" not in json.dumps(store.snapshot())
    record_failure(store, "sample", Unauthorized("SECRET"))
    assert store.snapshot()["accounts"][0]["status"] == "needs_attention"


def test_rate_limited_worker_does_not_try_another_account(store, monkeypatch):
    store.add_account(Account("other", "pw", "mail@example.org", {}))
    store.add_target("another")
    calls = []
    async def fail(*args):
        calls.append(args[2]["username"])
        return record_failure(store, args[2]["account"], TooManyRequests())
    monkeypatch.setattr("x_engine.worker.scan_target", fail)
    asyncio.run(run(store, once=True))
    assert len(calls) == 1


class Page(list):
    def __init__(self, ids, cursor=None, next_page=None):
        super().__init__({"id": str(i)} for i in ids)
        self.next_cursor = cursor
        self.next_page = next_page

    async def next(self):
        return self.next_page


def gather(store, page, cap=5, known=None):
    provider = XProvider(store)
    async def request(call, *args, **kwargs):
        return await call(*args, **kwargs)
    provider.request = request
    async def first():
        return page
    return asyncio.run(provider.pages(first, cap, lambda x: x, known))


def test_pinned_known_post_does_not_stop_pagination(store):
    page = Page([1, 4], "next", Page([3, 2], None))
    results, complete = gather(store, page, known={"1", "2"})
    assert complete and {r["id"] for r in results} == {"1", "2", "3", "4"}


def test_repeated_cursor_and_page_cap_are_incomplete(store):
    page = Page([1], "repeat", Page([2], "repeat"))
    assert gather(store, page)[1] is False
    assert gather(store, page, cap=1)[1] is False


def test_empty_page_with_cursor_is_not_terminal(store):
    page = Page([], "cursor", Page([2], None))
    result, complete = gather(store, page)
    assert complete and result == [{"id": "2"}]


def test_target_assignment_sticky_and_pause_preserves_data(store):
    store.add_account(Account("other", "pw", "mail@example.org", {}))
    assert store.add_target("second")["account"] == "other"
    assert store.add_target("target")["account"] == "sample"
    with pytest.raises(ValueError):
        store.add_target("bad-handle")
    with pytest.raises(ValueError):
        store.add_target("good", interval=1)


def test_target_assignment_prefers_a_verified_session(store):
    store.account_state('sample', 'ready')
    store.add_account(Account('unverified', 'pw', 'mail@example.org', {}))
    assert store.add_target('newtarget')['account'] == 'sample'


def test_only_one_worker(store):
    with worker_lock(store.directory):
        with pytest.raises(ValueError):
            with worker_lock(store.directory):
                pass


@pytest.mark.parametrize('reply_fails',[False,True])
def test_scan_only_reads_recent_pages_and_merges_replies(store,reply_fails):
    profile = SimpleNamespace(id="42", following_count=4000)
    calls=[]
    class FakeClient:
        async def get_user_by_screen_name(self, _name):
            return profile
        async def get_user_following(self,_id,count):
            calls.append(('following',count))
            return [SimpleNamespace(id='1',screen_name='one',name='One')]
        async def get_user_tweets(self,_id,stream,count):
            calls.append((stream,count))
            if stream=='Replies' and reply_fails:raise Forbidden()
            return [SimpleNamespace(id=stream,text='example',created_at='',favorite_count=0,
                retweet_count=0,reply_count=0,kind='reply' if stream=='Replies' else 'post')]
    class FakeProvider:
        @asynccontextmanager
        async def session(self, _account):
            yield FakeClient()
        async def request(self, call, *args, **kwargs):
            return await call(*args, **kwargs)
        async def pages(self,*args):
            raise AssertionError('Full pagination is forbidden')
    target = store.snapshot()["targets"][0]
    result = asyncio.run(scan_target(store, FakeProvider(), target, 5, 50))
    assert result == ('replies:target_unavailable' if reply_fails else 'ok')
    assert calls == [('following',20),('Tweets',20),('Replies',20)]
    assert store.snapshot()["counts"]["posts"] == (1 if reply_fails else 2)
    assert store.snapshot()['scans'][0]['complete']==int(not reply_fails)
    assert store.snapshot()["targets"][0]["following_started_at"]
    assert store.rows("SELECT * FROM following") == []
    assert store.feed('follow')['items'] == []


def test_scan_does_not_compare_different_identity(store):
    with store.db:
        store.db.execute("UPDATE targets SET user_id='original' WHERE username='target'")
    class FakeProvider:
        @asynccontextmanager
        async def session(self, _account):
            yield SimpleNamespace(get_user_by_screen_name=lambda _: None)
        async def request(self, *_args):
            return SimpleNamespace(id="replacement")
    result = asyncio.run(scan_target(store, FakeProvider(), store.snapshot()["targets"][0], 5, 50))
    assert result == "target_identity_changed"
    assert not store.snapshot()["scans"]


def test_dashboard_local_security_and_add_target(store):
    httpd = server(store.directory, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{httpd.server_port}"
    try:
        with urlopen(origin + "/api/status") as r:
            data = r.read().decode()
            assert "private-password" not in data
            assert json.loads(data)["counts"]["accounts"] == 1
        request = Request(origin + "/api/targets", data=b'{"username":"newone"}', headers={"Content-Type": "application/json"})
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 403
        request.add_header("Origin", origin)
        with urlopen(request) as response:
            assert json.load(response)["target"] == "newone"
        store.save_posts('newone',[{'id':'7','text':'Search me','url':'https://x.com/i/status/7','kind':'reply'}],True)
        with urlopen(origin+'/api/feed?kind=reply&q=Search') as response:
            assert json.load(response)['items'][0]['target']=='newone'
        check=Request(origin+'/api/targets/check',data=b'{"username":"newone"}',
                      headers={'Content-Type':'application/json','Origin':origin})
        with urlopen(check) as response:
            assert json.load(response)=={'queued':True}
        with store.db:store.db.execute("UPDATE targets SET enabled=0 WHERE username='newone'")
        with pytest.raises(HTTPError) as error:urlopen(check)
        assert error.value.code==400
        with pytest.raises(HTTPError) as error:
            urlopen(Request(origin + "/api/status", headers={"Host": "evil.test"}))
        assert error.value.code == 403
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()
