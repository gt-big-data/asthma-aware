"""Exercise the real API server inside the Linux container without cloud access."""
from datetime import datetime, timezone
import json
import os
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import uvicorn
from app.config import Settings
from app.main import create_app
from forecast_job.publication import publish, timestamp
from forecast_job.sample import generate_sample_forecast
from forecast_job.storage import MemoryStore

assert os.getuid() == 10001, 'Container must not run as root'
app = create_app(Settings(forecast_provider='gcs-sample'))
app.state.forecast_store = MemoryStore()
server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8080, log_level='warning'))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()


def request(path, expected):
    try:
        response = urlopen('http://127.0.0.1:8080' + path, timeout=5)
    except HTTPError as exc:
        response = exc
    assert response.status == expected, (path, response.status, expected)
    return json.loads(response.read())


try:
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    assert server.started, 'API did not start'
    assert request('/health', 200) == {'status': 'ok'}
    request('/dashboard?lat=33.749&lon=-84.388', 503)
    request('/internal/forecast?lat=33.725&lon=-84.375', 503)
    fixture = generate_sample_forecast('container-test', datetime.now(timezone.utc), profile='available')
    publish(app.state.forecast_store, fixture)
    assert request('/internal/forecast?lat=33.725&lon=-84.375', 200)['run_id'] == 'container-test'
    request('/internal/forecast?lat=1&lon=1', 404)
    request('/internal/forecast?lat=100&lon=1', 422)
    app.state.forecast_store = MemoryStore()
    expired = generate_sample_forecast('expired-test', datetime.now(timezone.utc), profile='expired')
    publish(app.state.forecast_store, expired, now=timestamp(expired['valid_from']))
    request('/internal/forecast?lat=33.725&lon=-84.375', 503)
    assert request('/health', 200) == {'status': 'ok'}
    print('PASS: non-root Linux container; real HTTP health, sample read, coverage, expiry, and unchanged dashboard')
finally:
    server.should_exit = True
    thread.join(timeout=5)
