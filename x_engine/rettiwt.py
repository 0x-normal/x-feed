"""Rettiwt adapter for bounded recent activity checks."""
import asyncio
import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from twikit import errors

from .provider import XProvider, SessionMismatch


class RettiwtError(Exception):
    pass


class MissingSessionCookies(Exception):
    pass


def raise_provider_error(error):
    kind = error.get('kind')
    allowed = {'Unauthorized', 'Forbidden', 'NotFound', 'TooManyRequests',
               'AccountSuspended', 'AccountLocked', 'InvalidSession', 'UserUnavailable'}
    if kind == 'MissingSessionCookies':
        raise MissingSessionCookies()
    if kind in allowed:
        cls = getattr(errors, kind)
        headers = {'x-rate-limit-reset': str(int(error['reset']))} if error.get('reset') else None
        raise cls('Rettiwt request failed', headers=headers)
    raise RettiwtError('Rettiwt request failed')


class RettiwtPage(list):
    def __init__(self, data, fetch_next):
        super().__init__(SimpleNamespace(**item) for item in data['items'])
        self.next_cursor = data.get('next') or None
        self.fetch_next = fetch_next

    async def next(self):
        return await self.fetch_next(self.next_cursor)


class RettiwtClient:
    def __init__(self, cookies):
        self.cookies = cookies
        self.root = Path(__file__).resolve().parent.parent / 'tools' / 'rettiwt'

    async def rpc(self, op, **kwargs):
        node = self.root / 'node_modules' / 'node-win-x64' / 'bin' / 'node.exe'
        binary = str(node) if node.exists() else shutil.which('node')
        if not binary or not (self.root / 'node_modules' / 'rettiwt-api').exists():
            raise RettiwtError('Run setup.ps1 to install Rettiwt.')
        options = {'creationflags': 0x08000000} if os.name == 'nt' else {}
        process = await asyncio.create_subprocess_exec(binary, str(self.root / 'bridge.mjs'),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, **options)
        try:
            payload = json.dumps({'cookies': self.cookies, 'op': op, **kwargs}).encode()
            stdout, _ = await asyncio.wait_for(process.communicate(payload), timeout=120)
            result = json.loads(stdout)
            if not result.get('ok'):
                raise_provider_error(result.get('error', {}))
            return result['data']
        except (ValueError, KeyError, asyncio.TimeoutError):
            raise RettiwtError('Rettiwt response unavailable') from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def user(self):
        return SimpleNamespace(**await self.rpc('self'))

    async def get_user_by_screen_name(self, username):
        return SimpleNamespace(**await self.rpc('user', id=username))

    async def get_user_by_id(self, user_id):
        return SimpleNamespace(**await self.rpc('user', id=user_id))

    async def get_user_tweets(self, user_id, _type='Tweets', count=40, cursor=None):
        data = await self.rpc('replies' if _type=='Replies' else 'posts', id=user_id, count=count, cursor=cursor)
        return RettiwtPage(data, lambda next_cursor: self.get_user_tweets(user_id, _type, count, next_cursor))

    async def get_user_following(self, user_id, count=20, cursor=None):
        data = await self.rpc('following', id=user_id, count=min(count,20), cursor=cursor)
        return RettiwtPage(data, lambda next_cursor: self.get_user_following(user_id, count, next_cursor))

    async def get_user_followers(self, user_id, count=100, cursor=None):
        data = await self.rpc('followers', id=user_id, count=min(count,100), cursor=cursor)
        return RettiwtPage(data, lambda next_cursor: self.get_user_followers(user_id, count, next_cursor))


class RettiwtProvider(XProvider):
    @asynccontextmanager
    async def session(self, username):
        secret = self.store.credentials(username)
        client = RettiwtClient(secret['cookies'])
        user = await self.request(client.user)
        if user.screen_name.lower() != username.lower():
            raise SessionMismatch()
        self.store.account_state(username, 'ready')
        yield client
