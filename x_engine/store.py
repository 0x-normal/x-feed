import json
import sqlite3
import time
from pathlib import Path

from .accounts import handle
from .vault import Vault


class Store:
    def __init__(self, directory="data"):
        self.directory = Path(directory).resolve()
        self.vault = Vault(self.directory)
        self.db = sqlite3.connect(self.directory / "engine.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY, secret BLOB NOT NULL,
                status TEXT NOT NULL DEFAULT 'unverified', reason TEXT,
                cooldown_until REAL NOT NULL DEFAULT 0, verified_at REAL
            );
            CREATE TABLE IF NOT EXISTS targets (
                username TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(username),
                interval_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                next_run REAL NOT NULL DEFAULT 0, last_run REAL, status TEXT DEFAULT 'pending',
                user_id TEXT, post_baseline INTEGER NOT NULL DEFAULT 0,
                following_baseline INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS posts (
                target TEXT NOT NULL REFERENCES targets(username), id TEXT NOT NULL,
                text TEXT NOT NULL, created_at TEXT, url TEXT NOT NULL,
                likes INTEGER, reposts INTEGER, replies INTEGER, views TEXT,
                first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                PRIMARY KEY(target,id)
            );
            CREATE TABLE IF NOT EXISTS following (
                target TEXT NOT NULL REFERENCES targets(username), user_id TEXT NOT NULL,
                username TEXT NOT NULL, name TEXT, PRIMARY KEY(target,user_id)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, target TEXT NOT NULL, kind TEXT NOT NULL,
                subject_id TEXT NOT NULL, detail TEXT NOT NULL, observed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY, target TEXT NOT NULL, kind TEXT NOT NULL,
                complete INTEGER NOT NULL, item_count INTEGER NOT NULL, observed_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS event_time ON events(observed_at DESC);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        # Additive migrations preserve existing collections and encrypted credentials.
        for table, fields in {
            'accounts': {'enabled': 'INTEGER NOT NULL DEFAULT 1', 'source_file': "TEXT NOT NULL DEFAULT ''"},
            'targets': {'added_at': 'REAL NOT NULL DEFAULT 0', 'following_started_at': 'REAL',
                        'display_name': "TEXT NOT NULL DEFAULT ''", 'avatar': "TEXT NOT NULL DEFAULT ''"},
            'scans': {'pages_read': 'INTEGER'},
            'posts': {'kind': "TEXT NOT NULL DEFAULT 'legacy'", 'author': "TEXT NOT NULL DEFAULT ''",
                      'author_name': "TEXT NOT NULL DEFAULT ''", 'avatar': "TEXT NOT NULL DEFAULT ''",
                      'context': "TEXT NOT NULL DEFAULT '{}'", 'published_at': 'REAL NOT NULL DEFAULT 0'},
        }.items():
            present = {row[1] for row in self.db.execute(f'PRAGMA table_info({table})')}
            for name, definition in fields.items():
                if name not in present:
                    self.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS following_windows (
                target TEXT PRIMARY KEY REFERENCES targets(username), items TEXT NOT NULL, observed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS following_seen (
                target TEXT NOT NULL REFERENCES targets(username), user_id TEXT NOT NULL,
                PRIMARY KEY(target,user_id)
            );
            CREATE TABLE IF NOT EXISTS blocked_accounts (username TEXT PRIMARY KEY, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS smart_accounts (
                username TEXT PRIMARY KEY, name TEXT NOT NULL, avatar TEXT NOT NULL, bio TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS smart_scans (
                subject_id TEXT PRIMARY KEY, username TEXT NOT NULL,
                account TEXT NOT NULL REFERENCES accounts(username), state TEXT NOT NULL DEFAULT 'pending',
                cursor TEXT, pages INTEGER NOT NULL DEFAULT 0, checked_at REAL, error TEXT,
                next_run REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS smart_matches (
                subject_id TEXT NOT NULL REFERENCES smart_scans(subject_id), user_id TEXT NOT NULL,
                username TEXT NOT NULL REFERENCES smart_accounts(username),
                PRIMARY KEY(subject_id,user_id), UNIQUE(subject_id,username)
            );
            CREATE TABLE IF NOT EXISTS smart_cursors (
                subject_id TEXT NOT NULL REFERENCES smart_scans(subject_id), cursor TEXT NOT NULL,
                PRIMARY KEY(subject_id,cursor)
            );
            CREATE INDEX IF NOT EXISTS smart_scan_queue ON smart_scans(state,next_run);
        ''')
        if 'following_count' not in {r[1] for r in self.db.execute('PRAGMA table_info(following_windows)')}:
            self.db.execute('ALTER TABLE following_windows ADD COLUMN following_count INTEGER')
        with self.db:
            self.db.execute('UPDATE targets SET added_at=? WHERE added_at=0', (time.time(),))

    def close(self):
        self.db.close()

    def rows(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    def add_account(self, account, source_file=''):
        with self.db:
            if self.db.execute('SELECT 1 FROM blocked_accounts WHERE username=?', (account.username,)).fetchone():
                return False
            cursor = self.db.execute("INSERT OR IGNORE INTO accounts(username,secret,source_file) VALUES (?,?,?)",
                                     (account.username, self.vault.seal(account.secret_json()), source_file))
        return bool(cursor.rowcount)

    def require_account(self, username):
        row = self.db.execute('SELECT enabled FROM accounts WHERE username=?', (username,)).fetchone()
        if not row or not row[0]:
            raise ValueError('This login account is excluded or unavailable.')

    def exclude_accounts(self, usernames, source_file):
        with self.db:
            for username in usernames:
                self.db.execute('INSERT OR REPLACE INTO blocked_accounts VALUES (?,?)', (username, 'UserReportedSuspended'))
                self.db.execute("UPDATE accounts SET enabled=0,status='excluded',reason='UserReportedSuspended',source_file=? WHERE username=?", (source_file, username))
        return len(usernames)

    def credentials(self, username):
        self.require_account(username)
        row = self.db.execute("SELECT secret FROM accounts WHERE username=?", (username,)).fetchone()
        if row is None:
            raise ValueError("Account not found.")
        return json.loads(self.vault.open(row[0]))

    def save_credentials(self, username, secret):
        with self.db:
            self.db.execute("UPDATE accounts SET secret=? WHERE username=?",
                            (self.vault.seal(json.dumps(secret)), username))

    def account_state(self, username, status, reason=None, cooldown=0):
        with self.db:
            self.db.execute("UPDATE accounts SET status=?,reason=?,cooldown_until=? WHERE username=? AND enabled=1",
                            (status, reason, cooldown, username))
            if status == "ready":
                self.db.execute("UPDATE accounts SET verified_at=? WHERE username=? AND enabled=1", (time.time(), username))

    def add_target(self, username, interval=60, account=None):
        username = handle(username)
        if interval < 60:
            raise ValueError("Polling interval must be at least 60 seconds.")
        if account:
            account = handle(account)
            self.require_account(account)
        else:
            existing = self.rows("SELECT t.account FROM targets t JOIN accounts a ON t.account=a.username WHERE t.username=? AND a.enabled=1", (username,))
            choices = self.rows("""SELECT a.username FROM accounts a LEFT JOIN targets t ON t.account=a.username
                WHERE a.enabled=1 AND a.status IN ('ready','unverified') GROUP BY a.username
                ORDER BY CASE a.status WHEN 'ready' THEN 0 ELSE 1 END, COUNT(t.username), a.rowid LIMIT 1""")
            if existing:
                account = existing[0]["account"]
            elif choices:
                account = choices[0]["username"]
            else:
                raise ValueError("Import an available account before adding targets.")
        with self.db:
            self.db.execute("""INSERT INTO targets(username,account,interval_seconds,added_at) VALUES (?,?,?,?)
                ON CONFLICT(username) DO UPDATE SET account=excluded.account,
                interval_seconds=excluded.interval_seconds,enabled=1,next_run=0""", (username, account, interval,time.time()))
        return {"target": username, "account": account, "interval_seconds": interval}

    def remove_target(self, username):
        username = handle(username)
        with self.db:
            if not self.db.execute("SELECT 1 FROM targets WHERE username=?", (username,)).fetchone():
                raise ValueError("Target not found.")
            for table in ("following_windows", "following_seen", "following", "posts", "events", "scans"):
                self.db.execute(f"DELETE FROM {table} WHERE target=?", (username,))
            self.db.execute("DELETE FROM targets WHERE username=?", (username,))
        return {"removed": True, "target": username}

    def setting(self, key, default="0"):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))

    def _event(self, target, kind, subject_id, detail, now):
        self.db.execute("INSERT INTO events(target,kind,subject_id,detail,observed_at) VALUES (?,?,?,?,?)",
                        (target, kind, subject_id, json.dumps(detail, ensure_ascii=False), now))
        if kind == 'follow_observed':
            from .smart_accounts import queue_scan
            account = self.db.execute('SELECT account FROM targets WHERE username=?', (target,)).fetchone()[0]
            queue_scan(self, subject_id, detail.get('username', ''), account)

    def save_posts(self, target, posts, complete):
        now = time.time()
        baseline = self.db.execute("SELECT post_baseline FROM targets WHERE username=?", (target,)).fetchone()[0]
        with self.db:
            for post in posts:
                new = not self.db.execute("SELECT 1 FROM posts WHERE target=? AND id=?", (target, post["id"])).fetchone()
                self.db.execute("""INSERT INTO posts(target,id,text,created_at,url,likes,reposts,replies,views,first_seen,last_seen,
                    kind,author,author_name,avatar,context,published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(target,id) DO UPDATE SET text=excluded.text,likes=excluded.likes,
                    reposts=excluded.reposts,replies=excluded.replies,views=excluded.views,last_seen=excluded.last_seen,
                    kind=excluded.kind,author=excluded.author,author_name=excluded.author_name,avatar=excluded.avatar,
                    context=excluded.context,published_at=excluded.published_at""",
                    (target, post["id"], post["text"], post.get("created_at"), post["url"],
                     post.get("likes"), post.get("reposts"), post.get("replies"),
                     post.get("views"), now, now, post.get('kind','post'), post.get('author',target),
                     post.get('author_name',target), post.get('avatar',''), json.dumps(post.get('context',{})),
                     post.get('published_at',now)))
                if new and baseline:
                    self._event(target, "post_observed", post["id"], {"text": post["text"], "url": post["url"]}, now)
            # First fetched window is a baseline, even when history exceeds the page cap.
            self.db.execute("UPDATE targets SET post_baseline=1 WHERE username=?", (target,))
            self.db.execute("INSERT INTO scans(target,kind,complete,item_count,observed_at) VALUES (?,?,?,?,?)",
                            (target, "posts", int(complete), len(posts), now))

    def save_following(self, target, users, complete):
        now = time.time()
        current = {u["id"]: u for u in users}
        with self.db:
            self.db.execute("INSERT INTO scans(target,kind,complete,item_count,observed_at) VALUES (?,?,?,?,?)",
                            (target, "following", int(complete), len(current), now))
            if not complete:
                return
            baseline = self.db.execute("SELECT following_baseline FROM targets WHERE username=?", (target,)).fetchone()[0]
            previous = {r["user_id"]: dict(r) for r in self.db.execute("SELECT * FROM following WHERE target=?", (target,))}
            if baseline:
                for uid in sorted(current.keys() - previous.keys()):
                    self._event(target, "followed", uid, current[uid], now)
                for uid in sorted(previous.keys() - current.keys()):
                    self._event(target, "unfollowed", uid, previous[uid], now)
            self.db.execute("DELETE FROM following WHERE target=?", (target,))
            self.db.executemany("INSERT INTO following VALUES (?,?,?,?)",
                                [(target, uid, u["username"], u.get("name", "")) for uid, u in current.items()])
            self.db.execute("UPDATE targets SET following_baseline=1 WHERE username=?", (target,))

    def save_recent_following(self, target, users, expected_count=None, pages_read=None):
        """Alert on unseen window IDs only when the profile's total count increases."""
        if not isinstance(expected_count, int) or isinstance(expected_count, bool) or expected_count < 0:
            return 'following_count_unavailable'
        ordered = list({u['id']:u for u in users}.values())
        if not ordered and expected_count != 0:
            return 'following_window_unavailable'
        previous = self.db.execute('SELECT items,following_count FROM following_windows WHERE target=?', (target,)).fetchone()
        now = time.time()
        with self.db:
            if previous and previous['following_count'] is not None:
                previous_ids = {u['id'] for u in json.loads(previous['items'])}
                delta = expected_count - previous['following_count']
                if delta > 0:
                    new_users = [u for u in ordered if u['id'] not in previous_ids]
                    for user in new_users[:delta]:
                        detail = {**user, 'strategy':'count_increase',
                                  'previous_count':previous['following_count'], 'current_count':expected_count}
                        self._event(target,'follow_observed',user['id'],detail,now)
            else:
                # Old windows lack a saved total. Start this strategy silently,
                # preserving collected content and earlier events.
                self.db.execute('UPDATE targets SET following_started_at=? WHERE username=?',(now,target))
            self.db.execute('INSERT OR REPLACE INTO following_windows(target,items,observed_at,following_count) VALUES (?,?,?,?)',
                            (target,json.dumps(ordered),now,expected_count))
            self.db.execute('INSERT INTO scans(target,kind,complete,item_count,observed_at,pages_read) VALUES (?,?,?,?,?,?)',
                            (target,'recent_following',1,len(ordered),now,pages_read))
        return 'ok'

    def feed(self, kind='all', target=None, query='', before=None, limit=50, exclude=None):
        kinds = {'post','reply','repost','quote','follow'}
        if kind not in kinds | {'all'}:
            raise ValueError('Unknown feed filter.')
        excluded = set(exclude or ())
        if not excluded <= kinds:
            raise ValueError('Unknown excluded activity type.')
        limit = min(max(int(limit),1),100)
        conditions, args = ['1=1'], []
        if kind != 'all':
            conditions.append('kind=?'); args.append(kind)
        if excluded:
            conditions.append('kind NOT IN (' + ','.join('?' for _ in excluded) + ')')
            args.extend(sorted(excluded))
        if target:
            conditions.append('target=?'); args.append(handle(target))
        if query:
            conditions.append("(text LIKE ? ESCAPE '\\' OR target LIKE ? ESCAPE '\\')")
            escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            args.extend(['%'+escaped+'%']*2)
        if before:
            conditions.append('sort_key<?'); args.append(before)
        sql='''WITH feed AS (
            SELECT 'p:'||p.target||':'||p.id AS key,p.target,p.kind,p.text,p.url,p.author,p.author_name,p.avatar,
                p.context,p.published_at AS timestamp,p.likes,p.reposts,p.replies,
                printf('%020.6f',p.published_at)||':p:'||p.target||':'||p.id AS sort_key
            FROM posts p WHERE p.kind!='legacy'
            UNION ALL
            SELECT 'f:'||e.id,e.target,'follow','', '',e.target,e.target,'',e.detail,e.observed_at,0,0,0,
                printf('%020.6f',e.observed_at)||':f:'||e.id
            FROM events e WHERE e.kind='follow_observed'
        ) SELECT * FROM feed WHERE '''+' AND '.join(conditions)+' ORDER BY sort_key DESC LIMIT ?'
        rows = self.rows(sql,(*args,limit+1))
        from .smart_accounts import result
        smart = {}
        for row in rows[:limit]:
            if row['kind'] == 'follow':
                subject = str(json.loads(row['context']).get('id', ''))
                if subject not in smart:
                    smart[subject] = result(self, subject)
                row['smart_accounts'] = smart[subject]
        return {'items':rows[:limit],'next':rows[limit-1]['sort_key'] if len(rows)>limit else None}

    def snapshot(self):
        return {
            "counts": {table: self.db.execute(f"SELECT COUNT(*) FROM {table}"+(' WHERE enabled=1' if table=='accounts' else '')).fetchone()[0]
                       for table in ("accounts", "targets", "posts", "events", "smart_accounts")},
            "accounts": self.rows("SELECT username,status,reason,cooldown_until,verified_at,source_file FROM accounts WHERE enabled=1 ORDER BY CASE status WHEN 'ready' THEN 0 ELSE 1 END,rowid"),
            "excluded_accounts": self.db.execute('SELECT COUNT(*) FROM accounts WHERE enabled=0').fetchone()[0],
            "targets": self.rows("SELECT * FROM targets ORDER BY username"),
            "events": self.rows("SELECT * FROM events ORDER BY id DESC LIMIT 100"),
            "posts": self.rows("SELECT * FROM posts ORDER BY first_seen DESC,id DESC LIMIT 100"),
            "scans": self.rows("SELECT * FROM scans ORDER BY id DESC LIMIT 100"),
            "global_cooldown_until": float(self.setting("global_cooldown_until")),
            "worker_heartbeat": float(self.setting("worker_heartbeat")),
            "provider": self.setting("provider", "twifork"),
        }
