"""Public Smart Account catalog and resumable follower checks."""
import csv
import gzip
import time
from pathlib import Path

from .accounts import handle
from .provider import user_data


def import_catalog(store, filename):
    path = Path(filename)
    opener = gzip.open if path.suffix == '.gz' else open
    accounts = {}
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or 'handle' not in reader.fieldnames:
            raise ValueError('Smart Account CSV must have a handle column.')
        for number, row in enumerate(reader, 2):
            try:
                username = handle(row.get('handle') or '')
            except ValueError:
                raise ValueError(f'Invalid Smart Account handle on CSV row {number}.') from None
            accounts[username] = (username, row.get('name') or username,
                                  row.get('avatar') or '', row.get('bio') or '')
    if not accounts:
        raise ValueError('Smart Account CSV is empty.')
    with store.db:
        # Validate the entire input before replacing the catalog. Exported
        # spFollowers counts are not evidence of a relationship to a project.
        store.db.execute('DELETE FROM smart_matches')
        store.db.execute('DELETE FROM smart_cursors')
        store.db.execute('DELETE FROM smart_accounts')
        store.db.executemany('INSERT INTO smart_accounts VALUES (?,?,?,?)', accounts.values())
        store.db.execute("UPDATE smart_scans SET state='pending',cursor=NULL,pages=0,checked_at=NULL,error=NULL,next_run=0")
        store.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('smart_catalog_source', path.name))
    return {'smart_accounts': len(accounts), 'source': path.name}


def ensure_catalog(store):
    if not store.setting('smart_catalog_source', ''):
        import_catalog(store, Path(__file__).with_name('smart-accounts.csv.gz'))


def queue_scan(store, subject_id, username, account):
    store.db.execute('''INSERT INTO smart_scans(subject_id,username,account) VALUES (?,?,?)
        ON CONFLICT(subject_id) DO UPDATE SET username=excluded.username''',
        (str(subject_id), username, account))
    row = store.db.execute('SELECT state,checked_at FROM smart_scans WHERE subject_id=?', (str(subject_id),)).fetchone()
    if row['state'] == 'complete' and (row['checked_at'] or 0) < time.time() - 86400:
        store.db.execute('DELETE FROM smart_matches WHERE subject_id=?', (str(subject_id),))
        store.db.execute('DELETE FROM smart_cursors WHERE subject_id=?', (str(subject_id),))
        store.db.execute("UPDATE smart_scans SET state='pending',cursor=NULL,pages=0,checked_at=NULL,error=NULL,next_run=0 WHERE subject_id=?", (str(subject_id),))


def result(store, subject_id, include_accounts=False):
    # The worker can commit a page between HTTP reads. Keep state, count, and
    # the clickable list in one read snapshot so they always agree.
    store.db.execute('SAVEPOINT smart_result')
    try:
        return _result(store, subject_id, include_accounts)
    finally:
        store.db.execute('RELEASE SAVEPOINT smart_result')


def _result(store, subject_id, include_accounts=False):
    subject_id = str(subject_id)
    row = store.db.execute('SELECT username,state,checked_at,pages,error FROM smart_scans WHERE subject_id=?', (subject_id,)).fetchone()
    data = dict(row) if row else {'state': 'pending', 'checked_at': None, 'pages': 0, 'error': None}
    data['subject_id'] = subject_id
    data['count'] = store.db.execute('SELECT COUNT(*) FROM smart_matches WHERE subject_id=?', (subject_id,)).fetchone()[0]
    if include_accounts:
        data['accounts'] = store.rows('''SELECT a.username,a.name,a.avatar,a.bio FROM smart_matches m
            JOIN smart_accounts a ON a.username=m.username WHERE m.subject_id=? ORDER BY a.username''', (subject_id,))
    return data


def save_page(store, job, users, cursor):
    subject = job['subject_id']
    cursor = cursor if cursor and cursor != '0' else None
    with store.db:
        if cursor and (cursor == job['cursor'] or store.db.execute(
                'SELECT 1 FROM smart_cursors WHERE subject_id=? AND cursor=?', (subject, cursor)).fetchone()):
            raise ValueError('Repeated follower cursor.')
        for user in users:
            username = handle(user['username'])
            store.db.execute('''INSERT OR IGNORE INTO smart_matches(subject_id,user_id,username)
                SELECT ?,?,username FROM smart_accounts WHERE username=?''', (subject, str(user['id']), username))
        if cursor:
            store.db.execute('INSERT INTO smart_cursors VALUES (?,?)', (subject, cursor))
        store.db.execute('''UPDATE smart_scans SET cursor=?,state=?,pages=pages+1,checked_at=?,
            error=NULL,next_run=? WHERE subject_id=?''',
            (cursor, 'checking' if cursor else 'complete', time.time(), time.time() + 5, subject))


async def check_next(store, provider):
    """One page per worker pass, sharing normal login and rate-limit controls."""
    from .worker import record_failure
    if float(store.setting('global_cooldown_until')) > time.time():
        return
    jobs = store.rows('''SELECT s.* FROM smart_scans s JOIN accounts a ON a.username=s.account
        WHERE s.state!='complete' AND s.next_run<=? AND a.enabled=1
        AND a.status IN ('ready','unverified','cooldown') AND a.cooldown_until<=?
        ORDER BY s.next_run,s.rowid LIMIT 1''', (time.time(), time.time()))
    if not jobs:
        return
    job = jobs[0]
    authenticating = True
    try:
        async with provider.session(job['account']) as client:
            authenticating = False
            page = await provider.request(client.get_user_followers, job['subject_id'], count=100, cursor=job['cursor'])
            if not page and not job['pages'] and getattr(page, 'next_cursor', None) in (None, '', '0'):
                profile = await provider.request(client.get_user_by_id, job['subject_id'])
                if type(getattr(profile, 'followers_count', None)) is not int or profile.followers_count != 0:
                    raise ValueError('Empty follower response without a confirmed zero follower count.')
            save_page(store, job, [user_data(u) for u in page], getattr(page, 'next_cursor', None))
    except Exception as exc:
        status = record_failure(store, job['account'], exc, authenticating)
        with store.db:
            store.db.execute("UPDATE smart_scans SET state='retrying',error=?,next_run=? WHERE subject_id=?",
                             (status, time.time() + (86400 if status == 'target_unavailable' else 300), job['subject_id']))
