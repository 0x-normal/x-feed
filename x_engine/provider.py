import asyncio
import time
from contextlib import asynccontextmanager

from twikit import Client


class SessionMismatch(Exception):
    pass


class GlobalCooldown(Exception):
    pass


class XProvider:
    def __init__(self, store, request_delay=3.0):
        self.store = store
        self.request_delay = max(1.0, request_delay)
        self.last_request = 0.0

    async def request(self, call, *args, **kwargs):
        if float(self.store.setting("global_cooldown_until")) > time.time():
            raise GlobalCooldown()
        return await call(*args, **kwargs)

    async def pace_http(self, _request):
        # The hook also covers requests made inside a library operation.
        if float(self.store.setting("global_cooldown_until")) > time.time():
            raise GlobalCooldown()
        await asyncio.sleep(max(0, self.last_request + self.request_delay - time.monotonic()))
        self.last_request = time.monotonic()

    @asynccontextmanager
    async def session(self, username):
        client = Client("en-US", timeout=30, event_hooks={"request": [self.pace_http]})
        try:
            secret = self.store.credentials(username)
            client.set_cookies(secret["cookies"])
            user = await self.request(client.user)
            if user.screen_name.lower() != username.lower():
                raise SessionMismatch()
            secret["cookies"] = client.get_cookies()
            self.store.save_credentials(username, secret)
            self.store.account_state(username, "ready")
            yield client
        finally:
            await client.http.aclose()

    async def pages(self, first, max_pages, convert, known=None):
        """Return unique items and whether we exhausted/connected to prior history."""
        page = await self.request(first)
        items, cursors = {}, set()
        for index in range(max_pages):
            ids = set()
            for item in page:
                converted = convert(item)
                items[converted["id"]] = converted
                ids.add(converted["id"])
            # Require the whole page to be previously observed, so a pinned old post
            # cannot hide newer posts on the next page.
            if known and ids and ids <= known:
                return list(items.values()), True
            cursor = getattr(page, "next_cursor", None)
            if not cursor or cursor == "0":
                return list(items.values()), True
            if cursor in cursors:
                return list(items.values()), False
            cursors.add(cursor)
            if index + 1 == max_pages:
                return list(items.values()), False
            page = await self.request(page.next)
        return list(items.values()), False


def post_data(post):
    return {"id": str(post.id), "text": post.text,
            "created_at": post.created_at, "url": f"https://x.com/i/status/{post.id}",
            "likes": post.favorite_count, "reposts": post.retweet_count,
            "replies": post.reply_count, "views": getattr(post, "view_count", None),
            "kind":getattr(post,'kind','post'), 'author':getattr(post,'author',''),
            'author_name':getattr(post,'author_name',''), 'avatar':getattr(post,'avatar',''),
            'context':getattr(post,'context',{}), 'published_at':getattr(post,'published_at',0)}


def user_data(user):
    followers = getattr(user, 'followers_count', None)
    return {"id": str(user.id), "username": user.screen_name, "name": user.name,
            'avatar': getattr(user, 'avatar', ''), 'bio': getattr(user, 'description', '') or '',
            'followers_count': followers if type(followers) is int and followers >= 0 else None}
