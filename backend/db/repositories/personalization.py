"""Private durable task records; caller identities never come from queue bodies."""
import os
from contextlib import contextmanager
from uuid import UUID, uuid5

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from db.config import get_database_url, _normalize_url


class TaskNotFound(LookupError):
    pass


class TaskConflict(ValueError):
    pass


@contextmanager
def connection(user_id=None, *, worker=False):
    url = os.getenv("WORKER_DATABASE_URL", "") if worker else get_database_url()
    if not url:
        raise RuntimeError("Task database is not configured")
    parsed = make_url(_normalize_url(url))
    if parsed.get_backend_name() != "postgresql" or parsed.query.get("sslmode") not in ("require", "verify-ca", "verify-full"):
        raise RuntimeError("Task database requires TLS")
    engine = create_engine(parsed, poolclass=NullPool, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"})
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE " + ("asthmaaware_worker" if worker else "asthmaaware_api")))
            if not worker:
                conn.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": str(user_id)})
            yield conn
    finally:
        engine.dispose()


def request_task(user_id: UUID, log_id: UUID, revision: int, idempotency_key: UUID):
    task_id = uuid5(user_id, str(idempotency_key))
    with connection(user_id) as conn:
        record = conn.execute(text("""SELECT id, revision, finalizes_at FROM app.symptom_logs
            WHERE id=:log_id AND user_id=:user_id"""), {"log_id": log_id, "user_id": user_id}).mappings().first()
        if record is None:
            raise TaskNotFound("Symptom log not found")
        if record["revision"] != revision:
            raise TaskConflict("Request the current symptom log revision")
        conn.execute(text("""INSERT INTO app.personalization_tasks
            (id,user_id,log_id,log_revision,available_at,status)
            VALUES (:id,:user_id,:log_id,:revision,:available_at,'pending_dispatch')
            ON CONFLICT DO NOTHING"""), {"id": task_id, "user_id": user_id,
                "log_id": log_id, "revision": revision, "available_at": record["finalizes_at"]})
        result = conn.execute(text("SELECT * FROM app.personalization_tasks WHERE id=:id AND user_id=:user_id"),
                              {"id": task_id, "user_id": user_id}).mappings().first()
        if result is None:
            # One task per revision prevents new idempotency keys from causing
            # repeated billable dispatch for the same observation.
            result = conn.execute(text("""SELECT * FROM app.personalization_tasks
                WHERE log_id=:log_id AND log_revision=:revision AND user_id=:user_id"""),
                {"log_id": log_id, "revision": revision, "user_id": user_id}).mappings().one()
        if result["log_id"] != log_id or result["log_revision"] != revision:
            raise TaskConflict("Idempotency key was already used for another request")
        return dict(result)


def get_task(user_id, task_id):
    with connection(user_id) as conn:
        row = conn.execute(text("SELECT * FROM app.personalization_tasks WHERE id=:id AND user_id=:user_id"),
                           {"id": task_id, "user_id": user_id}).mappings().first()
        if row is None:
            raise TaskNotFound("Task not found")
        return dict(row)


def mark_queued(user_id, task_id, cloud_name):
    with connection(user_id) as conn:
        conn.execute(text("""UPDATE app.personalization_tasks SET status='queued', cloud_task_name=:name, updated_at=now()
            WHERE id=:id AND user_id=:user_id AND status='pending_dispatch'"""),
            {"id": task_id, "user_id": user_id, "name": cloud_name})
    return get_task(user_id, task_id)


def handle_task(task_id):
    """Atomically acknowledge unavailable ML, without inventing output or retry storms."""
    with connection(worker=True) as conn:
        row = conn.execute(text("SELECT * FROM app.personalization_tasks WHERE id=:id FOR UPDATE"),
                           {"id": task_id}).mappings().first()
        if row is None:
            raise TaskNotFound("Task not found")
        if row["status"] not in ("queued", "pending_dispatch"):
            return dict(row)  # Redelivery is a no-op.
        if row["available_at"] > conn.scalar(text("SELECT now()")):
            raise TaskConflict("Task is not due yet")
        result = conn.execute(text("""UPDATE app.personalization_tasks SET status='blocked_ml_unavailable',
            reason='ML integration is not configured', attempts=attempts+1, updated_at=now()
            WHERE id=:id RETURNING *"""), {"id": task_id}).mappings().one()
        return dict(result)
