import asyncio
import json
import os
import time
from contextlib import contextmanager

from .provider import XProvider, post_data
from .following import fetch_window


def select_provider(store):
    if store.setting('provider', 'twifork') == 'rettiwt':
        from .rettiwt import RettiwtProvider
        return RettiwtProvider(store)
    return XProvider(store)


@contextmanager
def worker_lock(directory):
    """OS releases the lock on crashes; never unlink another process's lock file."""
    stream = open(directory / "worker.lock", "a+b")
    stream.seek(0)
    if os.fstat(stream.fileno()).st_size == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    acquired = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise ValueError("Another worker or login check is already running for this database.") from None
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def record_failure(store, account, exc, authenticating=False):
    # Exceptions may embed request headers or X response bodies. Persist only types.
    kind = type(exc).__name__
    now = time.time()
    if kind == "TooManyRequests":
        until = max(now + 60, getattr(exc, "rate_limit_reset", None) or now + 900) + 5
        store.account_state(account, "cooldown", "rate_limited", until)
        store.set_setting("global_cooldown_until", until)
        return "rate_limited"
    if kind == "GlobalCooldown":
        return "rate_limited"
    if kind in {"Unauthorized", "AccountLocked", "AccountSuspended", "SessionMismatch", "InvalidSession", "MissingSessionCookies"} or (authenticating and kind == "Forbidden"):
        store.account_state(account, "needs_attention", kind)
        return "account_needs_attention"
    if kind in {"Forbidden", "UserNotFound", "UserUnavailable", "NotFound"}:
        return "target_unavailable"
    store.account_state(account, "cooldown", kind, now + 300)
    return "provider_error:" + kind


async def verify_accounts(store, limit=1, username=None):
    provider = select_provider(store)
    rows = store.rows("SELECT username,cooldown_until FROM accounts WHERE enabled=1 " + ("AND username=? " if username else "") + "ORDER BY CASE status WHEN 'ready' THEN 0 ELSE 1 END,rowid LIMIT ?",
                      (username, limit) if username else (limit,))
    if not rows:
        raise ValueError("No matching accounts. Import accounts first.")
    results = []
    for row in rows:
        account = row["username"]
        if float(store.setting("global_cooldown_until")) > time.time():
            results.append({"account": account, "status": "rate_limited"})
            break
        if row["cooldown_until"] > time.time():
            results.append({"account": account, "status": "cooldown"})
            continue
        try:
            async with provider.session(account):
                status = "ready"
        except Exception as exc:
            status = record_failure(store, account, exc, authenticating=True)
        results.append({"account": account, "status": status})
        if status == "rate_limited":
            break
    return results


async def scan_target(store, provider, target, *_legacy_page_limits):
    username, account = target["username"], target["account"]
    authenticating = True
    try:
        store.require_account(account)
        async with provider.session(account) as client:
            authenticating = False
            user = await provider.request(client.get_user_by_screen_name, username)
            previous_id = target["user_id"]
            # Handles can change hands; require explicit reset before comparing identities.
            if previous_id and str(user.id) != previous_id:
                return "target_identity_changed"
            with store.db:
                store.db.execute("UPDATE targets SET user_id=?,display_name=?,avatar=? WHERE username=?",
                    (str(user.id),getattr(user,'name',username),getattr(user,'avatar',''),username))
            statuses=[]
            # Bounded 2-5 page window, with a count-increase guard for alerts.
            try:
                saved = store.rows('SELECT items,following_count FROM following_windows WHERE target=?',(username,))
                saved_count = len(json.loads(saved[0]['items'])) if saved and saved[0]['following_count'] is not None else 0
                users, pages_read = await fetch_window(provider,client,user.id,saved_count)
                statuses.append(store.save_recent_following(username,users,getattr(user,'following_count',None),pages_read))
            except Exception as exc:
                status=record_failure(store,account,exc)
                if status in {'rate_limited','account_needs_attention'}:
                    return status
                statuses.append('following:'+status)
            posts={}
            posts_complete=True
            for stream in ('Tweets','Replies'):
                try:
                    page=await provider.request(client.get_user_tweets,user.id,stream,count=20)
                    for post in page:
                        posts[str(post.id)]=post_data(post)
                except Exception as exc:
                    posts_complete=False
                    status=record_failure(store,account,exc)
                    if status in {'rate_limited','account_needs_attention'}:
                        if posts:store.save_posts(username,list(posts.values()),False)
                        return status
                    statuses.append(stream.lower()+':'+status)
            if posts:store.save_posts(username,list(posts.values()),posts_complete)
            return next((s for s in statuses if s!='ok'),'ok')
    except Exception as exc:
        return record_failure(store, account, exc, authenticating)


async def run(store, once=False, target_name=None):
    from .smart_accounts import ensure_catalog, check_next, check_next_lookup, queue_scan
    ensure_catalog(store)
    # Backfill cards collected before Smart Accounts were introduced.
    with store.db:
        for event in store.rows('''SELECT e.subject_id,e.detail,t.account FROM events e
                JOIN targets t ON t.username=e.target LEFT JOIN smart_scans s ON s.subject_id=e.subject_id
                WHERE e.kind='follow_observed' AND s.subject_id IS NULL ORDER BY e.id DESC'''):
            queue_scan(store, event['subject_id'], json.loads(event['detail']).get('username', ''), event['account'])
    provider = select_provider(store)
    while True:
        successful = True
        now = time.time()
        if not once:
            store.set_setting("worker_heartbeat", now)
        targets = store.rows("SELECT * FROM targets WHERE enabled=1 " + ("AND username=? " if target_name else "") + "ORDER BY next_run",
                             (target_name,) if target_name else ())
        if not targets:
            if once:
                raise ValueError("No enabled targets. Add a target first.")
        global_until = float(store.setting("global_cooldown_until"))
        for target in targets:
            if global_until > time.time():
                successful = False
                if once:
                    print(json.dumps({"target": target["username"], "status": "rate_limited", "retry_at": global_until}), flush=True)
                break
            if not once and target["next_run"] > time.time():
                continue
            account = store.rows("SELECT status,cooldown_until,enabled FROM accounts WHERE username=?", (target["account"],))[0]
            if not account['enabled']:
                status='account_excluded'
            elif account["status"] == "needs_attention":
                status = "account_needs_attention"
            elif account["cooldown_until"] > time.time():
                status = "account_cooldown"
            else:
                status = await scan_target(store, provider, target)
            now = time.time()
            with store.db:
                store.db.execute("UPDATE targets SET last_run=?,next_run=?,status=? WHERE username=?",
                                 (now, now + target["interval_seconds"], status, target["username"]))
            if not once:
                store.set_setting("worker_heartbeat", now)
            if status != "ok":
                successful = False
            print(json.dumps({"target": target["username"], "status": status}), flush=True)
            global_until = float(store.setting("global_cooldown_until"))
        await check_next_lookup(store, provider)
        await check_next(store, provider)
        if once:
            return successful
        await asyncio.sleep(5)
