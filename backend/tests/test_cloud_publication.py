from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from forecast_job.publication import available_forecast, encode, publish, read_current, restore_run, timestamp, verify_freshness
from forecast_job.sample import generate_sample_forecast
from forecast_job.storage import Conflict, MemoryStore


def fixture(run='run-a', now=None, profile='available'):
    return generate_sample_forecast(run, now or datetime.now(timezone.utc), profile=profile)


def test_readback_duplicate_and_conflicting_delivery():
    store = MemoryStore()
    payload = fixture()
    assert publish(store, payload)
    assert read_current(store)[0] == payload
    assert publish(store, payload) is False
    changed = json.loads(encode(payload))
    changed['forecasts'][0]['cells'][0]['sample_index'] = 999
    with pytest.raises(ValueError, match='reused'):
        publish(store, changed)
    assert read_current(store)[0] == payload


def test_failed_promotion_preserves_previous_and_can_resume():
    store = MemoryStore()
    now = datetime.now(timezone.utc)
    previous, candidate = fixture(now=now), fixture('run-b', now + timedelta(seconds=1))
    publish(store, previous)
    with pytest.raises(RuntimeError):
        publish(store, candidate, fail_before_promotion=True)
    assert read_current(store)[0] == previous
    assert publish(store, candidate)
    assert read_current(store)[0] == candidate


def test_out_of_order_and_concurrent_promotion_cannot_regress():
    now = datetime.now(timezone.utc)
    newest = fixture('run-new', now + timedelta(seconds=5))

    class RacingStore(MemoryStore):
        race = True

        def write(self, name, data, expected=0):
            if name == 'current.json' and self.race:
                self.race = False
                publish(self, newest)
            return super().write(name, data, expected)

    store = RacingStore()
    assert publish(store, fixture(now=now)) is False
    assert read_current(store)[0] == newest


def test_generation_pinning_and_integrity():
    store = MemoryStore()
    payload = fixture()
    publish(store, payload)
    name = 'runs/run-a/forecast.json'
    obj = store.read(name)
    store.write(name, b'corrupt new generation', obj.generation)
    assert read_current(store)[0] == payload  # manifest pins original generation
    store.versions[(name, obj.generation)] = type(obj)(b'corrupt pinned version', obj.generation)
    with pytest.raises(ValueError, match='integrity'):
        read_current(store)


def test_broken_current_is_not_treated_as_first_publication():
    store = MemoryStore()
    publish(store, fixture())
    del store.versions[('runs/run-a/manifest.json', store.read('runs/run-a/manifest.json').generation)]
    before = store.read('current.json')
    with pytest.raises(ValueError, match='missing'):
        publish(store, fixture('run-b', datetime.now(timezone.utc) + timedelta(seconds=1)))
    assert store.read('current.json') == before


@pytest.mark.parametrize('profile', ['future', 'expired'])
def test_unavailable_time_windows(profile):
    store = MemoryStore()
    payload = fixture(profile=profile)
    publish(store, payload, now=timestamp(payload['valid_from']))
    with pytest.raises(ValueError, match='expired|not yet'):
        available_forecast(store, 33.725, -84.375)


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(source='live'),
    lambda p: p.update(run_id='../escape'),
    lambda p: p.update(forecasts=[]),
    lambda p: p['forecasts'][0]['cells'][0].update(sample_index=float('nan')),
    lambda p: p['forecasts'][0]['cells'][0].update(lat=90),
])
def test_invalid_payload_not_published(mutation):
    store = MemoryStore()
    payload = fixture()
    mutation(payload)
    with pytest.raises((ValueError, KeyError)):
        publish(store, payload)
    assert not store.objects


def test_diagnostic_api_and_existing_health_dashboard_contracts():
    app = create_app(Settings(forecast_provider='gcs-sample'))
    app.state.forecast_store = MemoryStore()
    with TestClient(app) as client:
        assert client.get('/health').json() == {'status': 'ok'}
        assert client.get('/internal/forecast?lat=33.725&lon=-84.375').status_code == 503
        publish(app.state.forecast_store, fixture())
        response = client.get('/internal/forecast?lat=33.725&lon=-84.375')
        assert response.status_code == 200
        assert response.json()['source'] == 'sample'
        assert response.headers['X-AsthmaAware-Data-Source'] == 'sample'
        assert client.get('/internal/forecast?lat=1&lon=1').status_code == 404
        assert client.get('/internal/forecast?lat=99&lon=1').status_code == 422
        assert client.get('/dashboard?lat=33.749&lon=-84.388').status_code == 503


def test_disabled_provider_keeps_liveness_available():
    with TestClient(create_app(Settings())) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/internal/forecast?lat=33.725&lon=-84.375').status_code == 404


@pytest.mark.parametrize('profile', ['future', 'expired'])
def test_invalid_time_candidate_cannot_replace_good_run(profile):
    store = MemoryStore()
    original = fixture()
    publish(store, original)
    with pytest.raises(ValueError, match='Cannot promote'):
        publish(store, fixture('run-b', profile=profile))
    assert read_current(store)[0] == original


def test_explicit_rollback_restores_a_validated_retained_run():
    store = MemoryStore()
    first = fixture()
    second = fixture('run-b', datetime.now(timezone.utc) + timedelta(seconds=1))
    publish(store, first)
    publish(store, second)
    assert restore_run(store, first['run_id']) == first
    assert read_current(store)[0] == first
    with pytest.raises(KeyError):
        restore_run(store, 'missing-run')
    assert read_current(store)[0] == first


def test_pointer_switch_during_read_never_mixes_runs():
    now = datetime.now(timezone.utc)
    first, second = fixture(now=now), fixture('run-b', now + timedelta(seconds=1))

    class SwitchingStore(MemoryStore):
        switch = False

        def read(self, name, generation=None):
            obj = super().read(name, generation)
            if name == 'current.json' and self.switch:
                self.switch = False
                publish(self, second)
            return obj

    store = SwitchingStore()
    publish(store, first)
    store.switch = True
    assert read_current(store)[0] == first
    assert read_current(store)[0] == second


def test_freshness_monitor_detects_missed_daily_publication_before_horizon_expires():
    now = datetime.now(timezone.utc)
    assert verify_freshness(fixture(now=now), now=now) == 0
    overdue = fixture(now=now - timedelta(hours=27))
    assert timestamp(overdue['valid_from']) <= now < timestamp(overdue['valid_until'])
    with pytest.raises(ValueError, match='overdue'):
        verify_freshness(overdue, now=now)
