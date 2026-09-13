import asyncio
import json
from types import SimpleNamespace
import pytest
from x_engine.accounts import Account, import_files
from x_engine.store import Store
from x_engine.worker import verify_accounts
from x_engine.provider import user_data


def test_follow_profile_details_survive_collection_and_feed(store):
    store.save_recent_following('target', users(1), 1)
    profile = user_data(SimpleNamespace(id='2', screen_name='newuser', name='New User',
                        description='Builder <script>example</script>\nSecond line', followers_count=12345))
    store.save_recent_following('target', [profile, *users(1)], 2)
    detail = json.loads(store.feed('follow')['items'][0]['context'])
    assert detail['bio'] == 'Builder <script>example</script>\nSecond line'
    assert detail['followers_count'] == 12345
    assert len(store.feed('follow')['items']) == 1
    assert user_data(SimpleNamespace(id='3', screen_name='zero', name='Zero', followers_count=0))['followers_count'] == 0
    assert user_data(SimpleNamespace(id='4', screen_name='unknown', name='Unknown'))['followers_count'] is None


@pytest.fixture
def store(tmp_path):
    value=Store(tmp_path)
    value.add_account(Account('sample','pw','mail@example.org',{}))
    value.add_target('target')
    yield value
    value.close()


def users(*ids):
    return [dict(id=str(i),username='user'+str(i),name='Example') for i in ids]


def test_count_increase_caps_follow_events_and_repeated_reads_deduplicate(store):
    assert store.save_recent_following('target',users(3,2),4000)=='ok'
    assert store.feed('follow')['items']==[]
    store.save_recent_following('target',users(4,3,2,1),4001)
    assert [r['subject_id'] for r in store.snapshot()['events']]==['4']
    store.save_recent_following('target',users(4,3,2,1),4001)
    store.save_recent_following('target',users(1,4,3),4001)
    assert len(store.feed('follow')['items'])==1


def test_empty_response_preserves_count_and_ids(store):
    store.save_recent_following('target',users(1,2),4000)
    assert store.save_recent_following('target',[],4000)=='following_window_unavailable'
    assert store.save_recent_following('target',[],4001)=='following_window_unavailable'
    assert store.save_recent_following('target',users(3,1),4001)=='ok'
    assert store.save_recent_following('target',users(5,6),4001)=='ok'
    assert [r['subject_id'] for r in store.snapshot()['events']]==['3']


@pytest.mark.parametrize('count',[4000,3999])
def test_unchanged_or_decreased_count_suppresses_page_drift(store,count):
    store.save_recent_following('target',users(1,2),4000)
    store.save_recent_following('target',users(3,4),count)
    assert store.feed('follow')['items']==[]
    assert store.rows('SELECT following_count FROM following_windows')[0]['following_count']==count


def test_migrated_window_gets_silent_count_baseline(store):
    with store.db:
        store.db.execute("INSERT INTO following_windows(target,items,observed_at) VALUES ('target','[]',1)")
    store.save_recent_following('target',users(1,2),4000)
    assert store.feed('follow')['items']==[]


@pytest.mark.parametrize('invalid',[None,-1,True,'4001'])
def test_missing_or_invalid_count_does_not_change_baseline(store,invalid):
    store.save_recent_following('target',users(1),4000)
    before=store.rows('SELECT * FROM following_windows')
    assert store.save_recent_following('target',users(2),invalid)=='following_count_unavailable'
    assert store.rows('SELECT * FROM following_windows')==before


def test_refollow_after_observed_removal_can_alert_again(store):
    store.save_recent_following('target',users(1),1)
    store.save_recent_following('target',users(2,1),2)
    store.save_recent_following('target',users(1),1)
    store.save_recent_following('target',users(2,1),2)
    assert len(store.feed('follow')['items'])==2


def test_zero_baseline_and_target_update_preserve_start(store):
    store.save_recent_following('target',[],0)
    start=store.snapshot()['targets'][0]['following_started_at']
    store.add_target('target',120)
    store.save_recent_following('target',users(1),1)
    assert store.snapshot()['targets'][0]['following_started_at']==start
    assert len(store.feed('follow')['items'])==1


def test_excluded_accounts_cannot_be_selected_decrypted_or_verified(store,tmp_path):
    store.exclude_accounts(['sample'],'X accounts 125.txt')
    store.account_state('sample','ready')
    with pytest.raises(ValueError):store.credentials('sample')
    with pytest.raises(ValueError):store.add_target('new',account='sample')
    with pytest.raises(ValueError):store.add_target('new')
    with pytest.raises(ValueError):asyncio.run(verify_accounts(store,username='sample'))
    assert not store.add_account(Account('sample','pw','mail@example.org',{}))
    assert store.snapshot()['accounts']==[]
    assert store.snapshot()['excluded_accounts']==1
    forbidden=tmp_path/'X accounts 125.txt'
    forbidden.write_text('sample:SECRET')
    with pytest.raises(ValueError):import_files(store,[str(forbidden)])


def test_feed_filters_search_and_stable_pagination(store):
    posts=[dict(id=str(i),text='100%_ match' if i==2 else 'example',url='https://x.com/i/status/'+str(i),
                kind=kind,published_at=100) for i,kind in enumerate(['post','reply','quote','repost'])]
    store.save_posts('target',posts,True)
    store.save_posts('target',posts,True)
    assert len(store.feed()['items'])==4
    assert store.feed('reply')['items'][0]['key']=='p:target:1'
    assert len(store.feed(query='%_')['items'])==1
    assert store.feed(target='other')['items']==[]
    first=store.feed(limit=2)
    second=store.feed(before=first['next'],limit=2)
    assert len({r['key'] for r in first['items']+second['items']})==4
    assert second['next'] is None
    with pytest.raises(ValueError):store.feed('invalid')


def test_hidden_types_are_removed_before_pagination_and_can_be_restored(store):
    posts = [dict(id=str(i), text='example', url='https://x.com/i/status/'+str(i),
                  kind='reply' if i >= 4 else ['post', 'quote', 'repost', 'post'][i],
                  published_at=100+i) for i in range(70)]
    store.save_posts('target', posts, True)
    store.save_recent_following('target', users(1), 1)
    store.save_recent_following('target', users(2, 1), 2)
    first = store.feed(exclude=['reply'], limit=2)
    second = store.feed(exclude=['reply'], limit=2, before=first['next'])
    third = store.feed(exclude=['reply'], limit=2, before=second['next'])
    visible = first['items'] + second['items'] + third['items']
    assert len(visible) == len({r['key'] for r in visible}) == 5
    assert {r['kind'] for r in visible} == {'post', 'quote', 'repost', 'follow'}
    assert third['next'] is None
    assert store.feed(kind='reply', exclude=['reply']) == {'items': [], 'next': None}
    assert {r['kind'] for r in store.feed(exclude=['reply', 'quote', 'follow'])['items']} == {'post', 'repost'}
    assert store.feed(exclude=['post','reply','quote','repost','follow']) == {'items': [], 'next': None}
    assert store.feed(kind='reply')['items']
    with pytest.raises(ValueError):
        store.feed(exclude=['invalid'])
