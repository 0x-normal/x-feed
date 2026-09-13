import asyncio

import pytest
from twikit.errors import TooManyRequests, Forbidden

from x_engine.provider import user_data, post_data
from x_engine.rettiwt import RettiwtClient, RettiwtError, MissingSessionCookies, raise_provider_error


def test_error_mapping_preserves_reset_without_response_body():
    with pytest.raises(TooManyRequests) as caught:
        raise_provider_error({'kind': 'TooManyRequests', 'reset': 2000000000, 'body': 'SECRET'})
    assert caught.value.rate_limit_reset == 2000000000
    assert 'SECRET' not in str(caught.value)
    with pytest.raises(Forbidden):
        raise_provider_error({'kind': 'Forbidden'})
    with pytest.raises(MissingSessionCookies):
        raise_provider_error({'kind': 'MissingSessionCookies'})
    with pytest.raises(RettiwtError):
        raise_provider_error({'kind': 'untrusted-exception-name'})


def test_following_cursor_is_forwarded_for_bounded_window():
    client = RettiwtClient({})
    calls = []
    async def rpc(op, **kwargs):
        calls.append((op, kwargs))
        return {'items': [{'id': '1', 'screen_name': 'example', 'name': 'Example'}],
                'next': 'cursor-two' if kwargs['cursor'] is None else ''}
    client.rpc = rpc
    async def check():
        first = await client.get_user_following('42', 100)
        assert user_data(first[0]) == {'id': '1', 'username': 'example', 'name': 'Example',
                                       'avatar':'', 'bio':'', 'followers_count':None}
        second=await first.next()
        assert second.next_cursor is None
    asyncio.run(check())
    assert calls == [('following', {'id': '42', 'count': 20, 'cursor': None}),
                     ('following', {'id': '42', 'count': 20, 'cursor': 'cursor-two'})]


def test_timeline_metrics_fit_existing_storage_contract():
    client = RettiwtClient({})
    async def rpc(op, **kwargs):
        return {'items': [{'id':'100', 'text':'A post', 'created_at':'2026-09-13T00:00:00Z',
            'favorite_count':3, 'retweet_count':2, 'reply_count':1, 'view_count':10}], 'next':''}
    client.rpc = rpc
    page = asyncio.run(client.get_user_tweets('42'))
    post = post_data(page[0])
    assert (post['likes'], post['reposts'], post['views']) == (3, 2, 10)
    assert post['url'] == 'https://x.com/i/status/100'


def test_replies_use_the_separate_endpoint():
    client=RettiwtClient({})
    calls=[]
    async def rpc(op,**kwargs):
        calls.append(op)
        return {'items':[],'next':''}
    client.rpc=rpc
    asyncio.run(client.get_user_tweets('42','Replies',20))
    assert calls==['replies']
