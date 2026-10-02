"""Argon2id/JWT migration regressions. SQLite only, no provider network."""
import asyncio
import hashlib
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import bcrypt
import jwt
import pytest
from argon2 import PasswordHasher, Type
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.api import auth_router, stream_router
from app.api.dependencies import _authenticate_request, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core import security, token_store, user_cache
from app.core.password_work import run_password_work
from app.core.security import (
    hash_password, verify_password, password_needs_rehash, create_access_token,
    decode_access_token, is_supported_password_hash,
)
from app.models.auth import AppUser, AppSetting, RevokedToken
from app.services.auth_service import AuthService
from app.services.bootstrap import ensure_admin_bootstrap, _APPLIED_HASH_KEY


@pytest.fixture
def database():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    for table in (AppUser.__table__, AppSetting.__table__, RevokedToken.__table__):
        table.create(engine)
    sessions = sessionmaker(bind=engine)
    user_cache.invalidate_user()
    token_store._revoked_negative.clear()
    with sessions() as db:
        yield db, sessions
    user_cache.invalidate_user()
    token_store._revoked_negative.clear()
    engine.dispose()


def add_user(db, stored_hash=None, username='migration_user', role='user'):
    user = AppUser(username=username, role=role, is_active=True, token_version=0,
                   failed_login_attempts=0, password_hash=stored_hash or hash_password('Original!Password1'))
    db.add(user); db.commit(); db.refresh(user)
    return user


def legacy_bcrypt(password):
    return bcrypt.hashpw(password.encode('utf-8')[:72], bcrypt.gensalt(rounds=4)).decode()


def test_new_hash_argon2id_full_unicode_password_and_column_width():
    password = 'Ж'*40+'full-tail-one'
    value = hash_password(password)
    assert value.startswith('$argon2id$v=19$')
    assert len(value) <= 255
    assert verify_password(password, value)
    assert not verify_password('Ж'*40+'full-tail-two', value)
    assert not password_needs_rehash(value)
    assert is_supported_password_hash(value)
    assert hash_password(password) != value  # random salt per hash


def test_bcrypt_read_compatibility():
    value = legacy_bcrypt('Legacy!Password1')
    assert verify_password('Legacy!Password1', value)
    assert not verify_password('wrong-password', value)
    assert password_needs_rehash(value)
    assert is_supported_password_hash(value)


def test_argon2_old_parameters_can_verify_and_rehash():
    old = PasswordHasher(memory_cost=8192,time_cost=1,parallelism=1,type=Type.ID).hash('Old!Parameters1')
    assert verify_password('Old!Parameters1', old)
    assert password_needs_rehash(old)


@pytest.mark.parametrize('value', [
    '', 'not-a-hash', '$argon2id$malformed', '$2b$12$malformed',
    '$argon2id$v=19$m=999999999,t=3,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    '$argon2id$v=19$m=65536,t=999,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    '$argon2id$v=19$m=65536,t=3,p=1$!!!!!!!!!!!!!!!!!!!!!!$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    '$argon2id$v=19$m=65536,t=3,p=1$AAAAAAAAAAAAAAAAAAAAAB$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    '$2b$31$'+'a'*53,
])
def test_invalid_or_expensive_hash_rejected_before_kdf(value):
    assert not is_supported_password_hash(value)
    assert not verify_password('Any!Password1', value)


def test_plain_and_unsalted_sha256_rejected():
    assert not verify_password('Old!Password1', 'Old!Password1')
    assert not verify_password('Old!Password1', hashlib.sha256(b'Old!Password1').hexdigest())


@pytest.mark.parametrize('algorithm', ['bcrypt','salted-sha256'])
def test_login_migrates_hash_without_revoking_sessions(database, algorithm):
    db, _ = database
    password='Migration!Password1'
    old = legacy_bcrypt(password) if algorithm=='bcrypt' else security._legacy_salted_sha256(password)
    user = add_user(db, old)
    before,_,_=create_access_token(user.username, {'uid':user.id,'tv':user.token_version})
    response,error=AuthService.authenticate_by_password(db,user.username,password,'198.51.100.11')
    assert error is None
    db.refresh(user)
    assert user.password_hash.startswith('$argon2id$')
    assert user.token_version == 0
    assert not response.password_change_required
    assert decode_access_token(before)['tv']==user.token_version
    assert decode_access_token(response.access_token)['uid']==user.id
    assert verify_password(password,user.password_hash)


def test_wrong_login_does_not_migrate(database):
    db,_=database
    old=legacy_bcrypt('Original!Password1')
    user=add_user(db,old)
    response,error=AuthService.authenticate_by_password(db,user.username,'Wrong!Password1','198.51.100.12')
    assert response is None and error
    db.refresh(user)
    assert user.password_hash==old
    assert user.failed_login_attempts==1


def local_client(sessions):
    app=FastAPI()
    app.include_router(auth_router.router)
    app.include_router(stream_router.router)
    def session():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db]=session
    @app.get('/api/private')
    async def private(user=__import__('fastapi').Depends(get_current_user)):
        return {'id':user.id}
    return TestClient(app)


@pytest.mark.parametrize('password', ['A'*72, 'Ж'*36+'tail', 'A'*72+'suffix'])
def test_ambiguous_bcrypt_requires_explicit_full_password_change(database,password):
    db,sessions=database
    old=legacy_bcrypt(password)
    user=add_user(db,old)
    client=local_client(sessions)
    login=client.post('/api/auth/login',json={'username':user.username,'password':password})
    assert login.status_code==200
    assert login.json()['password_change_required'] is True
    assert login.json()['expires_in'] <= 600
    token=login.json()['access_token']
    assert decode_access_token(token)['pwd_reset'] is True
    assert client.get('/api/auth/me').json()['password_change_required'] is True
    assert client.get('/api/private').status_code==403
    assert client.post('/api/stream/token').status_code==403
    db.refresh(user)
    assert user.password_hash==old  # suffix was not silently chosen
    new='Ж'*40+'Chosen!FullPassword'
    changed=client.post('/api/auth/change-password',json={'current_password':password,'new_password':new})
    assert changed.status_code==200
    db.refresh(user)
    assert user.password_hash.startswith('$argon2id$') and user.token_version==1
    assert verify_password(new,user.password_hash)
    assert not verify_password('Ж'*40+'Wrong!FullPassword',user.password_hash)
    assert client.get('/api/auth/me').status_code==401
    fresh=client.post('/api/auth/login',json={'username':user.username,'password':new})
    assert fresh.status_code==200 and not fresh.json()['password_change_required']
    assert client.get('/api/private').status_code==200


def test_short_legacy_login_route_then_logout(database):
    db,sessions=database
    user=add_user(db,legacy_bcrypt('Login!Password1'))
    client=local_client(sessions)
    response=client.post('/api/auth/login',json={'username':user.username,'password':'Login!Password1'})
    assert response.status_code==200
    token=response.json()['access_token']
    assert client.get('/api/private').status_code==200
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/private',headers={'Authorization':f'Bearer {token}'}).status_code==401


def payload():
    now=int(time.time())
    return {'sub':'jwt_user','uid':1,'tv':0,'jti':'synthetic-test-jti','iat':now,'exp':now+300}


def encode(value,**kwargs):
    return jwt.encode(value,kwargs.get('key',settings.SECRET_KEY),algorithm=kwargs.get('algorithm',settings.ALGORITHM))


@pytest.mark.parametrize('claim', security.JWT_REQUIRED_CLAIMS)
def test_jwt_missing_claim_rejected(claim):
    value=payload();value.pop(claim)
    assert decode_access_token(encode(value)) is None


@pytest.mark.parametrize('key,value', [
    ('uid','1'),('uid',True),('uid',0),('tv','0'),('tv',False),('tv',-1),
    ('jti',''),('jti',None),('sub',''),('sub',1),('exp','9999999999'),('iat',True),
    ('pwd_reset','true'),('scope','admin'),
])
def test_jwt_invalid_claim_types_rejected(key,value):
    claims=payload();claims[key]=value
    assert decode_access_token(encode(claims)) is None


def test_jwt_wrong_signature_none_expiry_and_future_claims():
    claims=payload()
    assert decode_access_token(encode(claims,key='different-test-key-long-enough-for-hs256')) is None
    assert decode_access_token(jwt.encode(claims,None,algorithm='none')) is None
    for extra in ({'exp':int(time.time())-1},{'nbf':int(time.time())+3600},{'iat':int(time.time())+3600}):
        assert decode_access_token(encode({**claims,**extra})) is None
    assert decode_access_token(encode(claims))==claims


@pytest.mark.parametrize('claim',['sub','exp','iat','jti'])
def test_jwt_reserved_claim_override_not_allowed(claim):
    with pytest.raises(ValueError):
        create_access_token('user',{'uid':1,'tv':0,claim:'override'})


def test_jwt_recreated_username_cannot_reuse_old_token(database):
    db,_=database
    user=add_user(db,username='reusable')
    user_id=user.id
    old,_,_=create_access_token(user.username,{'uid':user_id,'tv':0})
    # Distinct IDs without relying on SQLite recycling rowids.
    user.username='retired'
    db.commit()
    replacement=add_user(db,username='reusable',role='admin')
    assert replacement.id!=user_id
    user_cache.invalidate_user()
    request=Request({'type':'http','method':'GET','path':'/api/private','headers':[]})
    with pytest.raises(HTTPException) as error:
        _authenticate_request(request,SimpleNamespace(credentials=old),db)
    assert error.value.status_code==401


def test_stream_scope_and_token_version_enforced(database):
    db,sessions=database
    user=add_user(db)
    token,_,_=create_access_token(user.username,{'uid':user.id,'tv':0})
    client=local_client(sessions)
    assert client.get('/api/stream/999?token='+token).status_code==401
    stream=client.post('/api/stream/token',headers={'Authorization':f'Bearer {token}'})
    assert stream.status_code==200
    scoped=stream.json()['stream_token']
    assert client.get('/api/private',headers={'Authorization':f'Bearer {scoped}'}).status_code==401
    user.token_version=1;db.commit();user_cache.invalidate_user()
    assert client.get('/api/auth/me',headers={'Authorization':f'Bearer {token}'}).status_code==401


def test_argon2_bootstrap_applies_once_and_never_reverts(database,monkeypatch):
    """Owner decision 2026-10-02: the panel password is fixed at "123".

    The env hash binds the account exactly once (provisioning). A later boot
    must not revert a panel-side password change, and a changed env hash is
    logged rather than applied — otherwise a stale env file silently resets the
    real password and revokes sessions.
    """
    db,_=database
    initial=hash_password('Initial!Password1')
    monkeypatch.setattr(settings,'ADMIN_PASSWORD_HASH',initial)
    ensure_admin_bootstrap(db)
    user=db.scalar(select(AppUser).where(AppUser.username=='admin'))
    assert user.password_hash==initial

    changed=hash_password('Changed!Password1')
    user.password_hash=changed;db.commit()
    ensure_admin_bootstrap(db)
    db.refresh(user)
    assert user.password_hash==changed and user.token_version==0

    rotated=hash_password('Rotated!Password1')
    monkeypatch.setattr(settings,'ADMIN_PASSWORD_HASH',rotated)
    ensure_admin_bootstrap(db);db.refresh(user)
    assert user.password_hash==changed, 'env rotation must not override the panel'
    assert user.token_version==0, 'no session revocation without an applied rotation'
    assert db.get(AppSetting,_APPLIED_HASH_KEY).value==initial


def test_workers_are_bounded_and_do_not_block_event_loop():
    active=0;peak=0;lock=threading.Lock()
    def work():
        nonlocal active,peak
        with lock:
            active+=1;peak=max(peak,active)
        time.sleep(.04)
        with lock:active-=1
    async def run():
        heartbeat=0
        async def tick():
            nonlocal heartbeat
            for _ in range(20):
                await asyncio.sleep(.005);heartbeat+=1
        await asyncio.gather(tick(),*(run_password_work(work) for _ in range(6)))
        return heartbeat
    assert asyncio.run(run())==20
    assert peak<=settings.PASSWORD_HASH_CONCURRENCY


def test_password_change_uses_current_db_hash_not_detached_cache(database):
    db,_=database
    user=add_user(db)
    detached=SimpleNamespace(id=user.id,password_hash=user.password_hash)
    user.password_hash=hash_password('Latest!Password1');user.token_version=1;db.commit()
    assert not AuthService.change_password_with_current(db,detached,'Original!Password1','Next!Password1')
    assert AuthService.change_password_with_current(db,detached,'Latest!Password1','Next!Password1')
    db.refresh(user)
    assert user.token_version==2 and verify_password('Next!Password1',user.password_hash)


def test_admin_password_hash_computed_before_row_lock(monkeypatch):
    from app.api import admin_router
    events = []
    target = SimpleNamespace(id=2, username='managed_user', role='user', is_active=True,
                             token_version=0, password_hash='old', failed_login_attempts=1, locked_until=None)
    async def worker(func, password):
        events.append('hash')
        return 'new-argon2-hash'
    db = Mock()
    def select_target(statement):
        assert statement._for_update_arg is not None
        events.append('lock')
        return target
    db.scalar.side_effect = select_target
    monkeypatch.setattr(admin_router, 'run_password_work', worker)
    monkeypatch.setattr(admin_router, '_audit', Mock())
    monkeypatch.setattr(admin_router, 'invalidate_user', Mock())
    asyncio.run(admin_router.admin_patch_user(2, admin_router.UserPatch(new_password='Chosen!Password1'),
                 Request({'type':'http','method':'PATCH','path':'/api/admin/users/2','headers':[]}),
                 db, SimpleNamespace(id=1, username='admin')))
    assert events == ['hash', 'lock']
    assert target.password_hash == 'new-argon2-hash' and target.token_version == 1
    db.commit.assert_called_once()
