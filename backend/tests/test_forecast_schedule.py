from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import json
import sys

import numpy as np
import pandas as pd
import pytest
from google.api_core.exceptions import PreconditionFailed

from scripts import check_forecast_update as probe
from scripts import check_forecast_freshness as freshness
from scripts import publish_dashboard_forecast as publisher
from app.services import forecast_publication_guard as guard
from ingestion.geos_cf import fetch_forecast as nasa

NOW = datetime(2026, 11, 20, 18, tzinfo=timezone.utc)
INIT = pd.Timestamp("2026-11-20T09:00")
OLD = SimpleNamespace(issued_at=datetime(2026, 11, 19, 9, tzinfo=timezone.utc))


@pytest.fixture
def inventories(monkeypatch):
    calls = []
    state = {"met_late": False, "missing_predictor": False, "analysis_late": False}
    forecast = pd.date_range(INIT + pd.Timedelta(minutes=30), periods=120, freq="h").values
    history = pd.date_range(INIT - pd.Timedelta(hours=221.5), periods=222, freq="h").values
    specs = [s for s in nasa.VARIABLES if s.name in probe.REQUIRED_VARIABLES]
    def read(fn, url, **kwargs):
        assert fn is nasa._read_inventory
        calls.append(url)
        collection = nasa.MET_COLLECTION if nasa.MET_COLLECTION in url else nasa.AQC_COLLECTION
        names = {s.source_name: f"{s.description} ({s.units})" for s in specs if s.collection == collection}
        axis = history if "/ana/" in url else forecast
        if collection == nasa.MET_COLLECTION and "/fcst/" in url:
            if state["met_late"]:
                axis = axis - np.timedelta64(24, "h")
            if state["missing_predictor"]:
                names.pop(next(s.source_name for s in specs if s.name == "specific_humidity"))
        if "/ana/" in url and state["analysis_late"]:
            axis = axis[:-1]
        return names, axis, 179.75
    monkeypatch.setattr(nasa, "remote_call", read)
    return state, calls


def test_unchanged_run_reads_only_one_inventory(inventories):
    current = SimpleNamespace(issued_at=INIT.tz_localize("UTC").to_pydatetime())
    assert probe.ready_run(current, NOW) is None
    assert len(inventories[1]) == 1


def test_new_run_needs_all_forecast_predictors_and_preceding_analysis(inventories):
    assert probe.ready_run(OLD, NOW) == INIT
    assert len(inventories[1]) == 4


@pytest.mark.parametrize("problem", ["met_late", "missing_predictor", "analysis_late"])
def test_partial_new_run_waits_without_launching(inventories, problem):
    inventories[0][problem] = True
    assert probe.ready_run(OLD, NOW) is None


def test_future_nasa_initialization_is_rejected(inventories):
    with pytest.raises(nasa.ValidationError):
        probe.ready_run(OLD, datetime(2026, 11, 20, 8, tzinfo=timezone.utc))


def test_launcher_pins_date_using_execution_local_override(monkeypatch):
    import google.auth
    from google.auth.transport import requests
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (Mock(), "test"))
    client = Mock()
    client.post.return_value.json.return_value = {"name": "operations/test"}
    session = Mock()
    session.__enter__ = Mock(return_value=client)
    session.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(requests, "AuthorizedSession", lambda credentials: session)
    assert probe.launch_publisher("test", "us-east1", "publisher", INIT) == "operations/test"
    client.post.assert_called_once_with(
        "https://run.googleapis.com/v2/projects/test/locations/us-east1/jobs/publisher:run",
        json={"overrides": {"containerOverrides": [{"env": [{
            "name": "FORECAST_RUN_DATE", "value": "2026-11-20",
        }]}]}}, timeout=30,
    )


def test_lease_is_generation_protected_and_released_on_failure():
    bucket = Mock()
    bucket.get_blob.return_value = None
    bucket.blob.return_value.generation = 7
    with pytest.raises(RuntimeError, match="pipeline failed"):
        with guard.publication_lease(bucket, now=NOW) as acquired:
            assert acquired
            raise RuntimeError("pipeline failed")
    assert bucket.blob.return_value.upload_from_string.call_args.kwargs["if_generation_match"] == 0
    bucket.blob.return_value.delete.assert_called_once_with(if_generation_match=7, timeout=10)


def test_live_lease_prevents_second_publisher():
    bucket = Mock()
    bucket.get_blob.return_value.generation = 8
    bucket.get_blob.return_value.download_as_bytes.return_value = json.dumps({
        "owner": "another-run", "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
    }).encode()
    with guard.publication_lease(bucket, now=NOW) as acquired:
        assert not acquired
    bucket.blob.assert_not_called()


def test_expired_lease_can_be_replaced_without_deleting_a_newer_owner():
    bucket = Mock()
    bucket.get_blob.return_value.generation = 8
    bucket.get_blob.return_value.download_as_bytes.return_value = json.dumps({
        "owner": "crashed-run", "expires_at": (NOW - timedelta(minutes=1)).isoformat(),
    }).encode()
    bucket.blob.return_value.generation = 9
    bucket.blob.return_value.delete.side_effect = PreconditionFailed("new owner already acquired")
    with guard.publication_lease(bucket, now=NOW) as acquired:
        assert acquired
    assert bucket.blob.return_value.upload_from_string.call_args.kwargs["if_generation_match"] == 8
    bucket.blob.return_value.delete.assert_called_once_with(if_generation_match=9, timeout=10)


def test_guarded_publisher_skips_before_model_or_field_downloads(monkeypatch):
    from app.services import gcs_forecast
    bucket = Mock()
    bucket.get_blob.return_value = None
    bucket.blob.return_value.generation = 1
    monkeypatch.setattr(gcs_forecast, "get_bucket", lambda name: bucket)
    monkeypatch.setattr(publisher.Settings, "from_env", lambda: SimpleNamespace(forecast_gcs_bucket="test"))
    monkeypatch.setattr(probe, "current_publication", lambda bucket: OLD)
    monkeypatch.setattr(probe, "ready_run", lambda *args: None)
    publish = Mock()
    monkeypatch.setattr(publisher, "_publish", publish)
    monkeypatch.setattr(sys, "argv", ["publisher", "--only-newer"])
    publisher.main()
    publish.assert_not_called()
    bucket.blob.return_value.delete.assert_called_once()


@pytest.mark.parametrize("requested,executes", [("2026-11-20", True), ("2026-11-19", False)])
def test_publisher_honors_execution_local_run_date(monkeypatch, requested, executes):
    from app.services import gcs_forecast
    bucket = Mock()
    bucket.get_blob.return_value = None
    bucket.blob.return_value.generation = 1
    monkeypatch.setattr(gcs_forecast, "get_bucket", lambda name: bucket)
    monkeypatch.setattr(publisher.Settings, "from_env", lambda: SimpleNamespace(forecast_gcs_bucket="test"))
    monkeypatch.setattr(probe, "current_publication", lambda bucket: OLD)
    monkeypatch.setattr(probe, "ready_run", lambda *args: pytest.fail("Pinned run must not rediscover the moving alias"))
    publish = Mock()
    monkeypatch.setattr(publisher, "_publish", publish)
    monkeypatch.setenv("FORECAST_RUN_DATE", requested)
    monkeypatch.setattr(sys, "argv", ["publisher", "--only-newer"])
    publisher.main()
    assert publish.call_count == int(executes)
    if executes:
        assert publish.call_args.args[0].run_date == INIT.date()


@pytest.mark.parametrize("age,passes", [(30, True), (38.5, True), (40, True), (40.1, False), (-1, False)])
def test_freshness_allows_bounded_publication_window(age, passes):
    run = SimpleNamespace(issued_at=NOW - timedelta(hours=age), risk_artifact="risk", map_artifact="map",
                          times=[NOW + timedelta(hours=1)])
    bucket = Mock()
    bucket.blob.return_value.exists.return_value = True
    if passes:
        freshness.check(run, bucket, NOW)
    else:
        with pytest.raises(RuntimeError, match="age"):
            freshness.check(run, bucket, NOW)
