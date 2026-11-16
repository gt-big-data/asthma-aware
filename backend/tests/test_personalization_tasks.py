"""Queue transport, signed worker identity and task API tests; no cloud calls."""
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists

from app.api import personalization as api
from app.auth import get_current_user_id
from app.services import tasks
from app import worker

ORIGIN = "https://worker.example.run.app"
SERVICE = "task-dispatch@asthma-aware.iam.gserviceaccount.com"


def settings(**changes):
    values = dict(tasks_enabled=True, cloud_project="asthma-aware", tasks_location="us-east1",
        tasks_queue="personalization", personalization_worker_url=ORIGIN, tasks_service_account_email=SERVICE)
    values.update(changes)
    return SimpleNamespace(**values)


def record():
    now = datetime.now(timezone.utc)
    return dict(id=uuid4(), log_id=uuid4(), log_revision=1, status="pending_dispatch", reason=None,
                attempts=0, available_at=now, created_at=now, updated_at=now)


def test_oidc_task_body_contains_only_durable_id_and_deduplicates():
    row = record()
    calls = []
    class Client:
        def create_task(self, **kwargs):
            calls.append(kwargs)
            if len(calls) > 1:
                raise AlreadyExists("already queued")
    client = Client()
    name = tasks.enqueue(settings(), row, client)
    assert tasks.enqueue(settings(), row, client) == name
    request = calls[0]["request"]
    target = request["task"]["http_request"]
    assert json.loads(target["body"]) == {"task_id": str(row["id"])}
    assert target["oidc_token"] == {"service_account_email": SERVICE, "audience": ORIGIN}
    assert target["url"] == ORIGIN + "/internal/tasks/personalization"
    assert calls[0]["timeout"] == 10 and calls[0]["retry"] is None
    assert request["task"]["dispatch_deadline"] == {"seconds": 60}


@pytest.mark.parametrize("changes", [{"tasks_enabled":False}, {"personalization_worker_url":"http://worker.example"},
    {"personalization_worker_url":"https://user:pass@worker.example"}, {"tasks_queue":"../bad"},
    {"tasks_service_account_email":"user@example.com"}])
def test_invalid_dispatch_configuration_makes_no_cloud_request(changes):
    class Client:
        def create_task(self, **kwargs):
            pytest.fail("invalid config reached cloud")
    with pytest.raises(tasks.TasksUnavailable):
        tasks.enqueue(settings(**changes), record(), Client())


def test_ambiguous_queue_error_is_sanitized_for_safe_retry():
    class Client:
        def create_task(self, **kwargs):
            raise TimeoutError("sensitive RPC internals")
    with pytest.raises(tasks.TasksUnavailable) as failure:
        tasks.enqueue(settings(), record(), Client())
    assert "sensitive" not in str(failure.value)
    assert "same idempotency key" in str(failure.value)


def test_user_task_api_auth_validation_dispatch_retry_and_private_status(monkeypatch):
    app = FastAPI()
    app.state.settings = settings()
    app.include_router(api.router)
    row = record()
    user = uuid4()
    calls = []
    def requested(user_id, log_id, revision, key):
        assert user_id == user
        calls.append("durable")
        return row
    monkeypatch.setattr(api.repository, "request_task", requested)
    monkeypatch.setattr(api.repository, "mark_queued", lambda u,t,n: {**row,"status":"queued"})
    monkeypatch.setattr(api.repository, "get_task", lambda u,t: row if u == user else pytest.fail("wrong user"))
    def dispatch(s,r):
        assert calls == ["durable"]
        return "queue/task"
    monkeypatch.setattr(api, "enqueue", dispatch)
    body = {"log_id":str(row["log_id"]),"log_revision":1,"idempotency_key":str(uuid4())}
    with TestClient(app) as client:
        assert client.post("/v1/personalization/tasks",json=body).status_code == 401
        app.dependency_overrides[get_current_user_id] = lambda: user
        assert client.post("/v1/personalization/tasks",json={**body,"user_id":str(uuid4())}).status_code == 422
        response = client.post("/v1/personalization/tasks",json=body)
        assert response.status_code == 202 and response.json()["status"] == "queued"
        assert response.headers["cache-control"] == "private, no-store"
        assert client.get(f"/v1/personalization/tasks/{row['id']}").status_code == 200
        monkeypatch.setattr(api.repository,"get_task",lambda *a: (_ for _ in ()).throw(api.repository.TaskNotFound()))
        assert client.get(f"/v1/personalization/tasks/{uuid4()}").status_code == 404


def test_disabled_task_api_does_not_create_database_record(monkeypatch):
    app = FastAPI()
    app.state.settings = settings(tasks_enabled=False)
    app.include_router(api.router)
    app.dependency_overrides[get_current_user_id] = uuid4
    monkeypatch.setattr(api.repository,"request_task",lambda *a: pytest.fail("disabled dispatch wrote database"))
    with TestClient(app) as client:
        assert client.post("/v1/personalization/tasks",json={"log_id":str(uuid4()),"log_revision":1,"idempotency_key":str(uuid4())}).status_code == 503


@pytest.fixture
def google_signer(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    response = SimpleNamespace(data=json.dumps({"one":pem}).encode(),status=200)
    monkeypatch.setattr(worker,"google_cert_request",lambda *a,**kw:response)
    def token(**changes):
        now = int(time.time())
        claims = dict(iss="https://accounts.google.com",aud=ORIGIN,iat=now,exp=now+300,
                      sub="123456789",email=SERVICE,email_verified=True)
        claims.update(changes)
        return jwt.encode(claims,key,algorithm="RS256",headers={"kid":"one"})
    return token


def test_worker_verifies_real_signature_audience_issuer_and_service_identity(google_signer):
    config = worker.WorkerSettings(ORIGIN,SERVICE)
    assert worker.verify_worker_token(google_signer(),config)["email"] == SERVICE
    for changes in ({"aud":"wrong"},{"iss":"https://attacker.example"},{"email":"other@example.com"},
                    {"email_verified":False},{"exp":int(time.time())-1}):
        with pytest.raises(HTTPException) as failure:
            worker.verify_worker_token(google_signer(**changes),config)
        assert failure.value.status_code == 401
    with pytest.raises(HTTPException) as failure:
        worker.verify_worker_token(google_signer(),worker.WorkerSettings())
    assert failure.value.status_code == 503
    assert worker.verify_worker_token(google_signer(),worker.WorkerSettings(ORIGIN+"/",SERVICE))["email"] == SERVICE
    with pytest.raises(HTTPException) as failure:
        worker.verify_worker_token(google_signer(),worker.WorkerSettings("https://:password@worker.example",SERVICE))
    assert failure.value.status_code == 503


def test_google_key_transport_failure_is_retryable_unavailable(monkeypatch):
    from google.auth.exceptions import TransportError
    def unavailable(*a,**kw):
        raise TransportError("private-network-detail")
    monkeypatch.setattr(worker,"google_cert_request",unavailable)
    with pytest.raises(HTTPException) as failure:
        worker.verify_worker_token("arbitrary-token",worker.WorkerSettings(ORIGIN,SERVICE))
    assert failure.value.status_code == 503
    assert "private-network" not in failure.value.detail


def test_worker_acknowledges_no_ml_with_no_fabricated_result(monkeypatch,google_signer):
    task = record()
    monkeypatch.setattr(worker.repository,"handle_task",lambda task_id:{**task,"status":"blocked_ml_unavailable","reason":"ML integration is not configured"})
    app = worker.create_worker(worker.WorkerSettings(ORIGIN,SERVICE))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/internal/tasks/personalization",json={"task_id":str(task["id"])}).status_code == 401
        headers={"Authorization":"Bearer "+google_signer()}
        response=client.post("/internal/tasks/personalization",headers=headers,json={"task_id":str(task["id"])})
        assert response.status_code == 200 and response.json()["result"] is None
        assert response.json()["status"] == "blocked_ml_unavailable"
        assert client.post("/internal/tasks/personalization",headers=headers,json={"task_id":str(task["id"]),"user_id":str(uuid4())}).status_code == 422


def test_postgres_worker_redelivery_is_idempotent(engine,monkeypatch):
    from contextlib import contextmanager
    from datetime import timedelta
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from db.repositories import personalization, application
    user = uuid4()
    now = datetime.now(timezone.utc)-timedelta(days=1)
    with Session(engine) as session, session.begin():
        session.execute(text("SET LOCAL ROLE asthmaaware_api"))
        session.execute(text("SELECT set_config('app.user_id',:id,true)"),{"id":str(user)})
        log=application.save_log(session,user,application.local_today(now,"America/New_York"),
            dict(severity=2,latitude=33.75,longitude=-84.39,location_source="selected",expected_revision=0),now)
        log_id=log.id
    @contextmanager
    def connection(user_id=None,worker=False):
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE "+("asthmaaware_worker" if worker else "asthmaaware_api")))
            if not worker:
                conn.execute(text("SELECT set_config('app.user_id',:id,true)"),{"id":str(user_id)})
            yield conn
    monkeypatch.setattr(personalization,"connection",connection)
    row=personalization.request_task(user,log_id,1,uuid4())
    assert personalization.request_task(user,log_id,1,uuid4())["id"] == row["id"]
    result=personalization.handle_task(row["id"])
    assert result["status"] == "blocked_ml_unavailable" and result["attempts"] == 1
    assert personalization.handle_task(row["id"])["attempts"] == 1
    with pytest.raises(personalization.TaskNotFound):
        personalization.get_task(uuid4(),row["id"])
