import asyncio
from types import SimpleNamespace
import pytest
from x_engine.following import fetch_window, FollowingWindowError


class Page(list):
    def __init__(self,index,fetch,cursor=None):
        super().__init__([SimpleNamespace(id=str(index),screen_name='user'+str(index),name='Example')])
        self.next_cursor=cursor
        self.fetch=fetch

    async def next(self):
        return await self.fetch(self.next_cursor)


class Provider:
    async def request(self,call,*args,**kwargs):
        return await call(*args,**kwargs)


@pytest.mark.parametrize('saved,expected',[(0,2),(20,2),(21,3),(40,3),(70,5),(4000,5)])
def test_recent_window_never_exceeds_smart_page_budget(saved,expected):
    calls=[]
    async def fetch(cursor):
        calls.append(cursor)
        return Page(len(calls),fetch,'cursor'+str(len(calls)))
    async def first(user_id,count):
        assert user_id=='42' and count==20
        return await fetch(None)
    items,pages=asyncio.run(fetch_window(Provider(),SimpleNamespace(get_user_following=first),'42',saved))
    assert pages==len(calls)==len(items)==expected


def test_window_exhaustion_stops_early():
    async def first(*args,**kwargs):return Page(1,None,None)
    items,pages=asyncio.run(fetch_window(Provider(),SimpleNamespace(get_user_following=first),'42',1000))
    assert pages==len(items)==1


def test_failed_second_page_does_not_return_partial_window():
    async def fail(cursor):raise RuntimeError('unavailable')
    async def first(*args,**kwargs):return Page(1,fail,'next')
    with pytest.raises(RuntimeError):
        asyncio.run(fetch_window(Provider(),SimpleNamespace(get_user_following=first),'42',0))


def test_repeated_cursor_rejects_partial_window():
    async def fetch(cursor):return Page(1,fetch,'repeat')
    async def first(*args,**kwargs):return await fetch(None)
    with pytest.raises(FollowingWindowError):
        asyncio.run(fetch_window(Provider(),SimpleNamespace(get_user_following=first),'42',1000))
