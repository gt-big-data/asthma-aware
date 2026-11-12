"""HTTP, real PostgreSQL transactions, migration-created grants and RLS."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app import dependencies
from app.auth import get_current_user_id
from app.config import Settings
from app.main import create_app
from db.models.application import OutboxEvent, Profile, SymptomLog, SymptomLogRevision
from db.repositories import application as repository

NOW = datetime(2026, 11, 1, 17, tzinfo=timezone.utc)
BODY = dict(severity=3, latitude=33.749, longitude=-84.388, location_source="current")


@pytest.fixture
def api(engine, monkeypatch):
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: sessionmaker(bind=engine, expire_on_commit=False))
    application = create_app(Settings())
    state = {"user": uuid4(), "now": NOW}
    application.dependency_overrides[get_current_user_id] = lambda: state["user"]
    application.dependency_overrides[dependencies.utc_now] = lambda: state["now"]
    with TestClient(application) as client:
        yield client, state


def owned_session(engine, user):
    session = Session(engine)
    session.execute(text("SET LOCAL ROLE asthmaaware_api"))
    session.execute(select(func.set_config("app.user_id", str(user), True)))
    return session


def test_create_retry_edit_and_conflict_are_one_active_observation(api, engine):
    client, state = api
    first = client.put("/v1/symptoms/2026-11-01", json=BODY)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["revision"] == 1
    assert data["status"] == "provisional"
    assert data["exposure_status"] == "pending"
    assert data["finalizes_at"] == "2026-11-02T05:00:00Z"
    assert client.put("/v1/symptoms/2026-11-01", json=BODY).json()["revision"] == 1
    changed = client.put("/v1/symptoms/2026-11-01", json={**BODY, "severity": 5, "expected_revision": 1})
    assert changed.status_code == 200, changed.text
    assert changed.json()["revision"] == 2
    assert client.put("/v1/symptoms/2026-11-01", json={**BODY, "expected_revision": 1}).status_code == 409
    with owned_session(engine, state["user"]) as session:
        assert session.scalar(select(func.count()).select_from(SymptomLog)) == 1
        assert session.scalar(select(func.count()).select_from(SymptomLogRevision)) == 2
        events = session.scalars(select(OutboxEvent)).all()
        assert len(events) == 2
        assert all(event.available_at == datetime(2026, 11, 2, 5, tzinfo=timezone.utc) for event in events)
        assert session.get(SymptomLogRevision, (UUID(data["id"]), 1)).severity == 3


def test_automatic_midnight_finalization_needs_no_status_job(api):
    client, state = api
    response = client.put("/v1/symptoms/2026-11-01", json=BODY)
    assert response.status_code == 200, response.text
    state["now"] = datetime(2026, 11, 2, 4, 59, tzinfo=timezone.utc)
    assert client.get("/v1/symptoms/2026-11-01").json()["status"] == "provisional"
    state["now"] = datetime(2026, 11, 2, 5, tzinfo=timezone.utc)
    assert client.get("/v1/symptoms/2026-11-01").json()["status"] == "final"


def test_backfill_limits_and_future_dates(api):
    client, _ = api
    assert client.put("/v1/symptoms/2026-10-27", json=BODY).json()["status"] == "final"
    for day in ("2026-10-26", "2026-11-02"):
        assert client.put("/v1/symptoms/" + day, json=BODY).status_code == 422


@pytest.mark.parametrize("changes", [
    {"severity": 0}, {"severity": 6}, {"severity": True}, {"severity": 2.5},
    {"latitude": 91}, {"longitude": -181}, {"location_source": "unknown"},
    {"user_id": str(uuid4())}, {"expected_revision": -1},
])
def test_rejects_invalid_input_and_protected_fields(api, changes):
    client, _ = api
    assert client.put("/v1/symptoms/2026-11-01", json={**BODY, **changes}).status_code == 422


def test_profile_timezone_and_existing_log_boundary_are_stable(api):
    client, state = api
    assert client.get("/v1/users/me").json()["timezone"] == "America/New_York"
    assert client.patch("/v1/users/me", json={"timezone": "not-a-zone"}).status_code == 422
    assert client.patch("/v1/users/me", json={"timezone": None}).status_code == 422
    assert client.patch("/v1/users/me", json={"user_id": str(uuid4())}).status_code == 422
    assert client.patch("/v1/users/me", json={"name": "Test", "timezone": "America/Los_Angeles"}).status_code == 200
    state["now"] = datetime(2026, 11, 2, 7, tzinfo=timezone.utc)  # Still Nov 1 in LA.
    data = client.put("/v1/symptoms/2026-11-01", json=BODY).json()
    assert data["finalizes_at"] == "2026-11-02T08:00:00Z"
    client.patch("/v1/users/me", json={"timezone": "America/New_York"})
    assert client.get("/v1/symptoms/2026-11-01").json()["finalizes_at"] == data["finalizes_at"]


def test_users_cannot_read_or_replace_each_others_logs(api, engine):
    client, state = api
    first_user = state["user"]
    client.put("/v1/symptoms/2026-11-01", json=BODY)
    state["user"] = uuid4()
    assert client.get("/v1/symptoms/2026-11-01").status_code == 404
    assert client.get("/v1/symptoms").json()["items"] == []
    assert client.put("/v1/symptoms/2026-11-01", json={**BODY, "severity": 1}).status_code == 200
    with owned_session(engine, state["user"]) as session:
        # Deliberately omit repository ownership filtering: RLS still hides A.
        assert session.scalar(select(Profile).where(Profile.user_id == first_user)) is None
        assert list(session.scalars(select(SymptomLog.user_id))) == [state["user"]]
        with pytest.raises(DBAPIError):
            session.execute(text("INSERT INTO app.profiles (user_id, timezone) VALUES (:id, 'UTC')"), {"id": first_user})


def test_missing_rls_context_denies_access_and_context_does_not_leak(engine):
    with Session(engine) as session:
        session.execute(text("SET LOCAL ROLE asthmaaware_api"))
        assert session.scalar(select(func.count()).select_from(Profile)) == 0
        session.execute(select(func.set_config("app.user_id", str(uuid4()), True)))
        session.rollback()
        assert session.scalar(text("SELECT current_user")) != "asthmaaware_api"
        assert session.scalar(text("SELECT current_setting('app.user_id', true)")) in (None, "")


def test_log_and_revision_roll_back_if_outbox_fails(api, engine):
    client, state = api
    from sqlalchemy import event

    def fail(*args):
        raise RuntimeError("simulated outbox failure")

    event.listen(OutboxEvent, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="outbox failure"):
            client.put("/v1/symptoms/2026-11-01", json=BODY)
    finally:
        event.remove(OutboxEvent, "before_insert", fail)
    with owned_session(engine, state["user"]) as session:
        assert session.scalar(select(func.count()).select_from(SymptomLog)) == 0
        assert session.scalar(select(func.count()).select_from(SymptomLogRevision)) == 0


def test_history_pagination_and_today(api):
    client, _ = api
    assert client.get("/v1/symptoms/today").status_code == 404
    for day in ("2026-10-30", "2026-10-31", "2026-11-01"):
        assert client.put("/v1/symptoms/" + day, json=BODY).status_code == 200
    first = client.get("/v1/symptoms", params={"limit": 2}).json()
    assert [item["log_date"] for item in first["items"]] == ["2026-11-01", "2026-10-31"]
    second = client.get("/v1/symptoms", params={"limit": 2, "before": first["next_before"]}).json()
    assert [item["log_date"] for item in second["items"]] == ["2026-10-30"]
    assert second["next_before"] is None
    assert client.get("/v1/symptoms/today").json()["log_date"] == "2026-11-01"
    assert client.get("/v1/symptoms", params={"start": "2026-11-01", "end": "2026-10-01"}).status_code == 422


def test_concurrent_creates_cannot_overwrite_each_other(api):
    from concurrent.futures import ThreadPoolExecutor
    client, _ = api
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(
            lambda severity: client.put("/v1/symptoms/2026-11-01", json={**BODY, "severity": severity}),
            [2, 4],
        ))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert client.get("/v1/symptoms/today").json()["revision"] == 1


def test_commit_failure_does_not_return_a_false_success(api, engine, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError
    client, state = api

    class FailingSession(Session):
        pass

    def fail_commit(session):
        raise SQLAlchemyError("simulated commit failure")

    event.listen(FailingSession, "before_commit", fail_commit)
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: sessionmaker(bind=engine, class_=FailingSession))
    response = client.put("/v1/symptoms/2026-11-01", json=BODY)
    assert response.status_code == 503
    with owned_session(engine, state["user"]) as session:
        assert session.scalar(select(func.count()).select_from(SymptomLog)) == 0


def test_runtime_cannot_modify_audit_history_or_write_model_weights(api, engine):
    client, state = api
    assert client.put("/v1/symptoms/2026-11-01", json=BODY).status_code == 200
    for statement in (
        "UPDATE app.symptom_log_revisions SET severity = 1",
        "DELETE FROM app.symptom_log_revisions",
        "INSERT INTO app.personalization_revisions (id) VALUES (gen_random_uuid())",
    ):
        with owned_session(engine, state["user"]) as session:
            with pytest.raises(DBAPIError) as denied:
                session.execute(text(statement))
            assert denied.value.orig.sqlstate == "42501"


def test_restricted_login_can_use_v1_without_database_owner_permissions(engine):
    from sqlalchemy import create_engine
    name = "asthmaaware_test_" + uuid4().hex
    password = "ephemeral_local_test_only"
    with engine.begin() as connection:
        connection.execute(text(f"CREATE ROLE {name} LOGIN PASSWORD '{password}' IN ROLE asthmaaware_api"))
    restricted = create_engine(engine.url.set(username=name, password=password))
    try:
        with owned_session(restricted, uuid4()) as session:
            repository.ensure_profile(session, uuid4())  # Wrong identity is rejected by RLS.
    except DBAPIError as denied:
        assert denied.orig.sqlstate == "42501"
    else:
        pytest.fail("a different profile must not be creatable")
    try:
        user = uuid4()
        with owned_session(restricted, user) as session:
            assert repository.ensure_profile(session, user).user_id == user
            session.commit()
    finally:
        restricted.dispose()
        with engine.begin() as connection:
            connection.execute(text(f"DROP ROLE {name}"))


@pytest.mark.parametrize("day, hours", [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
def test_midnight_boundary_handles_dst(day, hours):
    from zoneinfo import ZoneInfo
    start = datetime.combine(day, datetime.min.time(), ZoneInfo("America/New_York")).astimezone(timezone.utc)
    assert repository.midnight_after(day, "America/New_York") - start == timedelta(hours=hours)
