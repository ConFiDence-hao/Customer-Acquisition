"""The business clock must trust agreeing HTTPS samples rather than the host wall clock."""
import importlib
import importlib.util
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

UTC = timezone.utc
REFERENCE = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)


def clock_module():
    assert importlib.util.find_spec('app.clock') is not None, 'trusted business clock is not implemented'
    return importlib.import_module('app.clock')


def fixture_clock(offsets=(0, 1, 100), host_offset=-21600):
    module = clock_module()
    ticks = [100.0]
    host = [REFERENCE + timedelta(seconds=host_offset)]
    samples = dict(zip(module.PEERS, offsets))
    def fetch(url):
        offset = samples[url]
        if isinstance(offset, Exception):
            raise offset
        return module.TimeSample(url, REFERENCE + timedelta(seconds=offset), ticks[0])
    instance = module.TrustedClock(monotonic=lambda: ticks[0],
        host_now=lambda: host[0] + timedelta(seconds=ticks[0] - 100), fetcher=fetch)
    return instance, ticks, host, samples


def test_clock_ignores_host_offset():
    instance, ticks, host, samples = fixture_clock()
    assert instance.synchronize() is True
    assert abs((instance.utc_now() - REFERENCE).total_seconds()) <= 1
    assert instance.now().hour == 18 and instance.now().utcoffset() == timedelta(hours=8)
    ticks[0] += 10
    host[0] -= timedelta(days=3)
    assert abs((instance.utc_now() - REFERENCE).total_seconds() - 10) <= 1


@pytest.mark.parametrize('offsets', [(0, 30, 60), (0, RuntimeError('unavailable'), RuntimeError('unavailable'))])
def test_initial_sync_rejects_samples_without_two_agreeing_peers(offsets):
    module = clock_module()
    instance, _, _, _ = fixture_clock(offsets)
    with pytest.raises(module.ClockUnavailable):
        instance.synchronize()
    with pytest.raises(module.ClockUnavailable):
        instance.now()
    assert instance.writable() is False


@pytest.mark.parametrize('change', [
    {'age': '3'}, {'age': 'invalid'}, {'rtt': 2.1}, {'status_code': 302},
    {'redirected': True}, {'date': ''}, {'date': 'not a date'}, {'date': 'Wed, 14 Oct 2026 10:00:00'},
])
def test_invalid_https_date_samples_are_rejected(change):
    module = clock_module()
    fields = dict(peer=module.PEERS[0], date=format_datetime(REFERENCE, usegmt=True), age='0',
                  rtt=0.4, received_monotonic=100.0, status_code=200, redirected=False)
    fields.update(change)
    with pytest.raises(ValueError):
        module.parse_sample(**fields)


def test_https_date_sample_accounts_for_transmission_and_accepts_fresh_cache():
    module = clock_module()
    sample = module.parse_sample(peer=module.PEERS[0], date=format_datetime(REFERENCE, usegmt=True),
        age='2', rtt=0.4, received_monotonic=100.0, status_code=200, redirected=False)
    assert sample.received_monotonic == 100.0
    assert abs((sample.utc - REFERENCE).total_seconds() - 2.2) < 0.01


def test_network_rtt_excludes_local_certificate_store_initialization(monkeypatch):
    import httpx
    module=clock_module()
    ticks=[100.0]
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['verify'] is True and kwargs['follow_redirects'] is False
            ticks[0]+=2.1  # Slow Windows trust-store initialization before any network request.
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def head(self, peer, **kwargs):
            ticks[0]+=0.4
            return httpx.Response(200,headers={'Date':format_datetime(REFERENCE,usegmt=True)})
    monkeypatch.setattr(module.httpx,'Client',Client)
    monkeypatch.setattr(module,'_monotonic',lambda:ticks[0])
    sample=module.fetch_sample(module.PEERS[0])
    assert abs((sample.utc-REFERENCE).total_seconds()-0.2)<0.01


def test_temporary_outage_keeps_anchor_and_six_hour_outage_blocks_writes():
    instance, ticks, _, samples = fixture_clock((0, 0, 0))
    instance.synchronize()
    samples.update({peer: RuntimeError('offline') for peer in samples})
    ticks[0] += 900
    assert instance.synchronize() is False
    assert instance.status()['state'] == 'degraded'
    assert instance.writable() is True
    assert instance.utc_now() == REFERENCE + timedelta(minutes=15)
    ticks[0] += 21600
    assert instance.writable() is False and instance.status()['state'] == 'read_only'
    with pytest.raises(clock_module().ClockUnavailable):
        instance.ensure_writable()
    # Read time is still available without silently adopting the host clock.
    assert instance.utc_now() == REFERENCE + timedelta(seconds=22500)


def test_refresh_runs_at_fifteen_minutes_without_network_on_business_reads():
    instance, ticks, _, samples = fixture_clock((0, 0, 0))
    instance.synchronize()
    calls = []
    previous = instance.fetcher
    instance.fetcher = lambda peer: (calls.append(peer), previous(peer))[1]
    ticks[0] += 899
    instance.now();instance.status();instance.today()
    assert calls == []
    assert instance.refresh_if_due() is False
    ticks[0] += 1
    assert instance.refresh_if_due() is True
    assert len(calls) == 3


def test_host_clock_step_requests_resync_without_adopting_the_step():
    instance, ticks, host, samples = fixture_clock((0, 0, 0))
    instance.synchronize()
    ticks[0] += 1
    host[0] += timedelta(hours=1)
    assert instance.utc_now() == REFERENCE + timedelta(seconds=1)
    assert instance.refresh_if_due() is True


def test_verified_backward_correction_never_moves_business_time_backward():
    instance, ticks, _, samples = fixture_clock((0, 0, 0))
    instance.synchronize()
    ticks[0] += 10
    before = instance.utc_now()
    samples.update({peer: 8 for peer in samples})
    instance.synchronize()
    assert instance.utc_now() >= before
    ticks[0] += 1
    assert instance.utc_now() > before
    assert instance.status()['correction_seconds'] > 0
    ticks[0] += 30
    assert abs((instance.utc_now() - (REFERENCE + timedelta(seconds=39))).total_seconds()) < 0.01


def test_monotonic_reset_requests_verification_and_temporarily_blocks_writes():
    instance, ticks, _, _ = fixture_clock((0, 0, 0))
    instance.synchronize()
    before = instance.utc_now()
    ticks[0] -= 1
    assert instance.utc_now() >= before
    assert instance.writable() is False
    assert instance.refresh_if_due() is True
    assert instance.writable() is True


def test_first_sync_has_bounded_wait_when_peers_do_not_finish(monkeypatch):
    from threading import Event
    from time import monotonic
    module = clock_module()
    gate = Event()
    instance = module.TrustedClock(fetcher=lambda peer: gate.wait(5))
    monkeypatch.setattr(module, 'INITIAL_SYNC_BUDGET', 0.05)
    began = monotonic()
    try:
        with pytest.raises(module.ClockUnavailable):
            instance.synchronize()
        assert monotonic() - began < 0.5
    finally:
        gate.set()


def test_database_sessions_and_scoring_use_the_same_verified_time(tmp_path, monkeypatch):
    from app import accounts, db
    from app.analysis import analyze
    module = clock_module()
    instance, ticks, _, _ = fixture_clock((0, 0, 0))
    instance.synchronize()
    monkeypatch.setattr(module, 'CLOCK', instance)
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'clock-test-password')
    db.initialize()
    assert db.now() == '2026-10-14T18:00:00+08:00'
    with db.connect() as c:
        uid = c.execute('SELECT id FROM users').fetchone()[0]
    accounts.create_session(uid)
    with db.connect() as c:
        row = c.execute('SELECT expires_at,created_at FROM sessions').fetchone()
    assert row['expires_at'].startswith('2026-10-21T18:00:00')
    assert row['created_at'] == '2026-10-14T18:00:00+08:00'
    item = analyze({'title': '智慧焚烧控制项目招标', 'url': 'https://example.com/clock',
                    'source_id': 'cecep', 'source_kind': '集团采购',
                    'published_at': '2026-10-14T17:00:00+08:00', 'content': ''}, [], db.DEFAULT_SETTINGS)
    assert item['analysis_date'] == '2026-10-14'
    assert not any('未来' in risk for risk in item['risks'])


def test_readonly_expiry_check_never_writes_and_sql_guard_rejects_mutation(tmp_path, monkeypatch):
    import sqlite3
    from app import db
    from app.analysis import analyze
    module = clock_module()
    instance, ticks, _, _ = fixture_clock((0, 0, 0))
    instance.synchronize()
    monkeypatch.setattr(module, 'CLOCK', instance)
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'clock-test-password')
    db.initialize()
    db.upsert(analyze({'title': '智慧焚烧项目招标', 'url': 'https://example.com/stale-clock',
                      'source_id': 'cecep', 'published_at': '2026-10-14', 'content': ''}, [], db.DEFAULT_SETTINGS))
    with db.connect() as c:
        before = tuple(c.execute('SELECT payload,updated_at,last_seen_at FROM opportunities').fetchone())
        c.execute("UPDATE opportunities SET payload=json_set(payload,'$.analysis_date','2020-01-01')")
        before = tuple(c.execute('SELECT payload,updated_at,last_seen_at FROM opportunities').fetchone())
    ticks[0] += 21601
    db.refresh_expired()
    with db.connect() as c:
        assert tuple(c.execute('SELECT payload,updated_at,last_seen_at FROM opportunities').fetchone()) == before
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE opportunities SET note='forbidden'")


def test_application_initializes_clock_before_database_and_exposes_safe_public_time(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main
    module = clock_module()
    instance, _, _, _ = fixture_clock((0, 0, 0))
    monkeypatch.setattr(module, 'CLOCK', instance)
    order = []
    real_db = db.initialize
    def initialize_clock():
        order.append('clock')
        instance.synchronize()
    def initialize_db():
        order.append('database')
        real_db()
    monkeypatch.setattr(module, 'initialize', initialize_clock)
    monkeypatch.setattr(db, 'initialize', initialize_db)
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'clock-test-password')
    monkeypatch.setenv('SESSION_SECRET', 'clock-test-session-secret-32-chars')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    with TestClient(main.app) as client:
        assert order == ['clock', 'database']
        result = client.get('/api/clock')
        assert result.status_code == 200
        assert result.json()['current_time'].startswith('2026-10-14T18:00:00')
        assert 'peers' not in result.json() and 'host_offset_seconds' not in result.json()


def test_open_connection_cannot_reuse_prepared_write_after_clock_expires(tmp_path, monkeypatch):
    import sqlite3
    from app import db
    module=clock_module()
    instance,ticks,_,_=fixture_clock((0,0,0))
    instance.synchronize()
    monkeypatch.setattr(module,'CLOCK',instance)
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setattr(db,'ROOT',tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD','clock-test-password')
    db.initialize()
    with db.connect() as c:
        c.execute('UPDATE settings SET value=value')
        ticks[0]+=21601
        with pytest.raises(sqlite3.DatabaseError):
            c.execute('UPDATE settings SET value=value')
