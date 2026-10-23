"""Exercise run rollback using real SQL transactions without needing PostGIS."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.repositories import ingestion


@pytest.mark.parametrize("sql_failure", [False, True])
def test_failed_run_rolls_back_its_writes_without_committing_caller_work(monkeypatch, sql_failure):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE readings (id INTEGER PRIMARY KEY)"))

    run = SimpleNamespace(status="running")
    monkeypatch.setattr(ingestion, "start_run", lambda *args, **kwargs: run)

    def finish(session, run, **kwargs):
        run.status = kwargs["status"]
        run.error = kwargs["error"]

    monkeypatch.setattr(ingestion, "finish_run", finish)
    with Session(engine) as session:
        session.execute(text("INSERT INTO readings VALUES (1)"))
        with pytest.raises((RuntimeError, IntegrityError)):
            with ingestion.run_scope(session, "test"):
                session.execute(text("INSERT INTO readings VALUES (2)"))
                if sql_failure:
                    session.execute(text("INSERT INTO readings VALUES (2)"))
                raise RuntimeError("upstream failed")
        assert run.status == "failed"
        assert session.execute(text("SELECT id FROM readings")).scalars().all() == [1]
        # SQL errors inside the run do not poison the outer session.
        session.execute(text("INSERT INTO readings VALUES (3)"))
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM readings")) == 0
    engine.dispose()
