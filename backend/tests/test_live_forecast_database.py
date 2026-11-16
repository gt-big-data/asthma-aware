"""Database adapter contract tests and opt-in disposable Postgres/RLS tests."""
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.services.live_forecast import ForecastUnavailable
from db.repositories import live_forecast as repository
from tests.test_live_forecast import INIT, artifact


def test_missing_or_non_tls_credentials_fail_before_connect(monkeypatch):
    monkeypatch.delenv("FORECAST_PUBLISH_DATABASE_URL", raising=False)
    with pytest.raises(ForecastUnavailable):
        with repository.connection(writer=True):
            pytest.fail("missing credentials reached a connection")
    monkeypatch.setenv("FORECAST_PUBLISH_DATABASE_URL", "postgresql://user:local-test-password@localhost/postgres")
    with pytest.raises(ForecastUnavailable, match="TLS"):
        with repository.connection(writer=True):
            pytest.fail("non-TLS connection was opened")


def test_publication_uses_one_transaction_and_duplicate_does_not_write(monkeypatch):
    class Result:
        def scalar(self):
            return None
    class Connection:
        def __init__(self):
            self.calls = []
        def execute(self, statement, values):
            self.calls.append((str(statement), values))
            return Result()
        def scalar(self, statement):
            return artifact()
    conn = Connection()
    @contextmanager
    def connection(writer=False):
        assert writer
        yield conn
    monkeypatch.setattr(repository, "connection", connection)
    assert repository.publish(artifact()) is False
    assert len(conn.calls) == 1
    sql, values = conn.calls[0]
    assert "ON CONFLICT" in sql and "EXCLUDED.initialized_at >" in sql
    assert values["initialized"] == INIT
    changed = artifact()
    changed["cells"][0]["hours"][0]["values"]["pm25"] = 40
    with pytest.raises(ForecastUnavailable, match="different published snapshot"):
        repository.publish(changed)


def test_alembic_sql_generation_contains_forecast_grants_and_rls():
    output = StringIO()
    cfg = Config("alembic.ini", output_buffer=output)
    cfg.attributes["database_url"] = "postgresql+psycopg://offline@localhost/offline"
    command.upgrade(cfg, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE forecast.current" in sql
    assert "ALTER TABLE forecast.current FORCE ROW LEVEL SECURITY" in sql
    assert "GRANT SELECT ON forecast.current TO asthmaaware_api" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON forecast.current TO asthmaaware_forecast_writer" in sql
    assert "Existing forecast writer role has unsafe privileges" in sql
    assert "0003_live_forecast" in sql


def test_postgres_publication_idempotency_and_no_regression(engine, monkeypatch):
    @contextmanager
    def connection(writer=False):
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE " + ("asthmaaware_forecast_writer" if writer else "asthmaaware_api")))
            yield conn
    monkeypatch.setattr(repository, "connection", connection)
    assert repository.publish(artifact()) is True
    assert repository.publish(artifact()) is False
    assert repository.read_latest() == artifact()
    older = artifact()
    older["initialized_at"] = (INIT - timedelta(days=1)).isoformat()
    older["run_id"] = "20261114_09z"
    for row in older["cells"][0]["hours"]:
        from app.services.live_forecast import moment
        row["timestamp"] = (moment(row["timestamp"]) - timedelta(days=1)).isoformat()
    assert repository.publish(older) is False
    assert repository.read_latest()["run_id"] == "20261115_09z"


def test_postgres_forecast_reader_cannot_write_and_writer_cannot_read_symptoms(engine):
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE asthmaaware_api"))
        assert conn.scalar(text("SELECT count(*) FROM forecast.current")) >= 0
        with pytest.raises(DBAPIError):
            conn.execute(text("DELETE FROM forecast.current"))
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE asthmaaware_forecast_writer"))
        with pytest.raises(DBAPIError):
            conn.execute(text("SELECT user_id FROM app.symptom_logs"))
