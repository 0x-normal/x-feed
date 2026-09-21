import asyncio
import csv
import gzip
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from twikit.errors import TooManyRequests

from x_engine.accounts import Account
from x_engine.rettiwt import RettiwtClient, RettiwtPage
from x_engine.smart_accounts import (check_next, check_next_lookup, import_catalog,
                                     lookup_result, queue_lookup, queue_scan, result, save_page)
from x_engine.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'data')
    value.add_account(Account('sample', 'synthetic', 'test@example.org', {}))
    value.add_target('watcher')
    source = tmp_path / 'catalog.csv'
    source.write_text('handle,name,avatar,bio,spFollowers\nAlice,Alice,,,999\n@BOB,Bob,,,123\nalice,Alice updated,,,999\n', encoding='utf-8')
    assert import_catalog(value, source)['smart_accounts'] == 2
    yield value
    value.close()


def queue(store, subject='42'):
    with store.db:
        queue_scan(store, subject, 'project', 'sample')
    return store.rows('SELECT * FROM smart_scans WHERE subject_id=?', (subject,))[0]


def user(uid, username):
    return {'id': uid, 'username': username}


def test_catalog_import_is_case_insensitive_atomic_and_ignores_export_counts(store, tmp_path):
    assert store.rows('SELECT name FROM smart_accounts WHERE username=?', ('alice',))[0]['name'] == 'Alice updated'
    assert result(store, '42')['count'] == 0
    bad = tmp_path / 'bad.csv'
    bad.write_text('handle,name\nalice,Alice\nnot a handle,Invalid\n')
    with pytest.raises(ValueError):
        import_catalog(store, bad)
    assert len(store.rows('SELECT * FROM smart_accounts')) == 2


def test_count_matches_list_and_resumes_without_duplicates(store):
    job = queue(store)
    save_page(store, job, [user('1', 'ALICE'), user('2', 'outsider')], 'next')
    first = result(store, '42', True)
    assert first['state'] == 'checking' and first['count'] == 1
    assert [a['username'] for a in first['accounts']] == ['alice']
    store.close()
    reopened = Store(store.directory)
    try:
        job = queue(reopened)
        assert job['cursor'] == 'next'
        save_page(reopened, job, [user('1', 'alice'), user('3', 'Bob')], '0')
        final = result(reopened, '42', True)
        assert final['state'] == 'complete'
        assert final['count'] == len(final['accounts']) == 2
    finally:
        reopened.close()


def test_dashboard_lookup_resolves_handle_and_queues_scan(store):
    class LookupProvider(Provider):
        async def get_user_by_screen_name(self, username):
            self.calls.append(('lookup', username))
            return SimpleNamespace(id='77', screen_name=username, name='Project')

    with store.db:
        queued = queue_lookup(store, '@Project', 'sample')
    assert queued['username'] == 'project' and queued['state'] == 'pending'
    provider = LookupProvider()
    asyncio.run(check_next_lookup(store, provider))
    data = lookup_result(store, 'project', True)
    assert data['subject_id'] == '77' and data['state'] == 'pending'
    assert data['count'] == 0 and data['accounts'] == []
    assert provider.calls[-1] == ('lookup', 'project')


def test_cycle_and_bad_page_preserve_previous_progress(store):
    save_page(store, queue(store), [user('1', 'alice')], 'next')
    before = store.rows('SELECT * FROM smart_scans')
    with pytest.raises(ValueError):
        save_page(store, queue(store), [user('3', 'bob')], 'next')
    assert store.rows('SELECT * FROM smart_scans') == before
    assert result(store, '42')['count'] == 1
    with pytest.raises(ValueError):
        save_page(store, queue(store), [user('3', 'bob'), user('4', '<bad>')], None)
    assert result(store, '42')['count'] == 1


def test_follow_events_queue_once_and_feed_uses_current_matches(store):
    store.save_recent_following('watcher', [], 0)
    project = {'id': '42', 'username': 'project', 'name': 'Project'}
    store.save_recent_following('watcher', [project], 1)
    save_page(store, queue(store), [user('1', 'alice')], None)
    item = store.feed('follow')['items'][0]
    assert item['smart_accounts']['count'] == 1
    assert json.loads(item['context'])['id'] == '42'
    store.save_recent_following('watcher', [], 0)
    store.save_recent_following('watcher', [project], 1)
    assert len(store.rows('SELECT * FROM smart_scans')) == 1
    assert result(store, '42')['state'] == 'complete'
    with store.db:
        store.db.execute('UPDATE smart_scans SET checked_at=?', (time.time() - 90000,))
    queue(store)
    assert result(store, '42')['count'] == 0
    assert result(store, '42')['state'] == 'pending'


class Provider:
    def __init__(self, page=None, error=None, followers_count=0):
        self.page, self.error, self.followers_count = page, error, followers_count
        self.calls = []

    @asynccontextmanager
    async def session(self, account):
        self.calls.append(('session', account))
        yield self

    async def request(self, call, *args, **kwargs):
        return await call(*args, **kwargs)

    async def get_user_followers(self, subject, **kwargs):
        self.calls.append((subject, kwargs))
        if self.error:
            raise self.error
        return self.page

    async def get_user_by_id(self, subject):
        return SimpleNamespace(id=subject, followers_count=self.followers_count)


def test_worker_one_page_and_rate_limit_keep_cursor_and_stop_all_sessions(store):
    queue(store)
    page = RettiwtPage({'items': [{'id': '1', 'screen_name': 'alice', 'name': 'Alice'}], 'next': 'more'}, None)
    provider = Provider(page)
    asyncio.run(check_next(store, provider))
    assert provider.calls[-1] == ('42', {'count': 100, 'cursor': None})
    with store.db:
        store.db.execute('UPDATE smart_scans SET next_run=0')
    provider.error = TooManyRequests('synthetic', headers={'x-rate-limit-reset': str(int(time.time()) + 600)})
    asyncio.run(check_next(store, provider))
    row = store.rows('SELECT * FROM smart_scans')[0]
    assert row['cursor'] == 'more' and row['state'] == 'retrying'
    assert result(store, '42')['count'] == 1
    calls = len(provider.calls)
    asyncio.run(check_next(store, provider))
    assert len(provider.calls) == calls
    assert float(store.setting('global_cooldown_until')) > time.time()


@pytest.mark.parametrize('followers_count,state', [(0, 'complete'), (12, 'retrying'), (None, 'retrying')])
def test_empty_first_response_is_only_zero_when_confirmed(store, followers_count, state):
    queue(store)
    provider = Provider(RettiwtPage({'items': [], 'next': ''}, None), followers_count=followers_count)
    asyncio.run(check_next(store, provider))
    assert result(store, '42')['state'] == state


def test_rettiwt_followers_use_full_page_and_forward_cursor():
    client = RettiwtClient({})
    calls = []
    async def rpc(op, **kwargs):
        calls.append((op, kwargs))
        return {'items': [], 'next': 'next' if not kwargs['cursor'] else ''}
    client.rpc = rpc
    async def read():
        first = await client.get_user_followers('42', 200)
        await first.next()
    asyncio.run(read())
    assert calls == [('followers', {'id': '42', 'count': 100, 'cursor': None}),
                     ('followers', {'id': '42', 'count': 100, 'cursor': 'next'})]


def test_bundled_catalog_contains_all_supplied_accounts():
    path = Path(__file__).resolve().parents[1] / 'x_engine' / 'smart-accounts.csv.gz'
    with gzip.open(path, 'rt', encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len({r['handle'].lower() for r in rows}) == 55626
