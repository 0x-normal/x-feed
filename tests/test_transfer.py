import json
import pytest
from x_engine.accounts import Account
from x_engine.store import Store
from x_engine.transfer import export_bundle, restore_bundle
from x_engine.worker import worker_lock


PASSPHRASE='synthetic-transfer-passphrase-only'


def source(tmp_path):
    store=Store(tmp_path/'source')
    store.add_account(Account('login','synthetic-secret','sample@example.org',{'auth_token':'synthetic-cookie'}))
    store.add_account(Account('excluded','pw','sample@example.org',{}))
    store.exclude_accounts(['excluded'],'excluded-source')
    store.account_state('login','ready')
    store.add_target('target',account='login')
    store.save_recent_following('target',[{'id':'1','username':'one'}],100,2)
    store.set_setting('worker_heartbeat',123)
    return store


def test_encrypted_transfer_preserves_sessions_baselines_and_exclusions(tmp_path):
    store=source(tmp_path)
    bundle=tmp_path/'backup.xengine'
    try:
        export_bundle(store.directory,bundle,PASSPHRASE)
        assert b'synthetic-secret' not in bundle.read_bytes()
        assert b'synthetic-cookie' not in bundle.read_bytes()
        assert store.setting('worker_heartbeat')=='123'
        restore_bundle(bundle,tmp_path/'restored',PASSPHRASE)
        restored=Store(tmp_path/'restored')
        try:
            assert restored.credentials('login')['password']=='synthetic-secret'
            assert restored.snapshot()['excluded_accounts']==1
            with pytest.raises(ValueError):restored.credentials('excluded')
            assert restored.snapshot()['accounts'][0]['status']=='ready'
            assert restored.setting('worker_heartbeat')=='0'
            assert restored.rows('SELECT following_count FROM following_windows')[0]['following_count']==100
        finally:restored.close()
    finally:store.close()


def test_transfer_rejects_wrong_passphrase_tampering_and_overwrite(tmp_path):
    store=source(tmp_path)
    bundle=tmp_path/'backup.xengine'
    try:
        export_bundle(store.directory,bundle,PASSPHRASE)
        with pytest.raises(ValueError):export_bundle(store.directory,bundle,PASSPHRASE)
        with pytest.raises(ValueError):restore_bundle(bundle,tmp_path/'wrong','wrong-passphrase-long-enough')
        assert not (tmp_path/'wrong').exists()
        with pytest.raises(ValueError):restore_bundle(bundle,store.directory,PASSPHRASE)
        data=json.loads(bundle.read_bytes());data['token']='invalid'
        bundle.write_text(json.dumps(data))
        with pytest.raises(ValueError):restore_bundle(bundle,tmp_path/'tampered',PASSPHRASE)
        assert not (tmp_path/'tampered').exists()
    finally:store.close()


def test_export_requires_stopped_collector(tmp_path):
    store=source(tmp_path)
    try:
        with worker_lock(store.directory):
            with pytest.raises(ValueError):export_bundle(store.directory,tmp_path/'backup.xengine',PASSPHRASE)
    finally:store.close()
