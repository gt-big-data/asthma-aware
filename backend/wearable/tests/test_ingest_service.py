import datetime as dt
import json

import pytest
from conftest import PINNED_NOW, PINNED_TODAY, TEST_USER

from app.services import ingest_service as service
from app.services import sample_store


def sample(metric="resting_heart_rate", days_ago=0, value=62.0):
    return (metric, PINNED_TODAY - dt.timedelta(days=days_ago), value)


# --- validate_sample ----------------------------------------------------


def test_a_plausible_sample_is_accepted():
    assert service.validate_sample("resting_heart_rate", PINNED_TODAY, 62.0, PINNED_TODAY) is None


def test_unknown_metrics_are_rejected():
    reason = service.validate_sample("blood_pressure", PINNED_TODAY, 120.0, PINNED_TODAY)
    assert reason == service.REASON_UNKNOWN_METRIC


@pytest.mark.parametrize("value", [0.0, 29.9, 150.1, -5.0])
def test_impossible_heart_rates_are_rejected(value):
    """A dropped sensor contact reports 0 bpm; stored, it would wreck the median."""
    reason = service.validate_sample("resting_heart_rate", PINNED_TODAY, value, PINNED_TODAY)
    assert reason == service.REASON_OUT_OF_RANGE


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_rejected(value):
    """NaN compares false against every bound, so it needs its own check."""
    reason = service.validate_sample("resting_heart_rate", PINNED_TODAY, value, PINNED_TODAY)
    assert reason == service.REASON_NOT_A_NUMBER


def test_range_edges_are_inclusive():
    assert service.validate_sample("spo2", PINNED_TODAY, 100.0, PINNED_TODAY) is None
    assert service.validate_sample("spo2", PINNED_TODAY, 70.0, PINNED_TODAY) is None
    assert service.validate_sample("nocturnal_cough_count", PINNED_TODAY, 0.0, PINNED_TODAY) is None


def test_a_days_grace_tolerates_a_device_in_an_eastern_timezone():
    tomorrow = PINNED_TODAY + dt.timedelta(days=1)
    assert service.validate_sample("steps", tomorrow, 5000.0, PINNED_TODAY) is None


def test_dates_beyond_the_grace_window_are_rejected():
    far_future = PINNED_TODAY + dt.timedelta(days=2)
    reason = service.validate_sample("steps", far_future, 5000.0, PINNED_TODAY)
    assert reason == service.REASON_FUTURE_DATE


def test_a_reset_device_clock_is_rejected():
    reason = service.validate_sample("steps", dt.date(1970, 1, 1), 5000.0, PINNED_TODAY)
    assert reason == service.REASON_TOO_OLD


def test_a_genuine_backfill_is_allowed():
    a_year_ago = PINNED_TODAY - dt.timedelta(days=365)
    assert service.validate_sample("steps", a_year_ago, 5000.0, PINNED_TODAY) is None


# --- today_local --------------------------------------------------------


def test_today_is_the_local_calendar_date():
    # 02:00 UTC on the 16th is still the 15th in Atlanta.
    late_utc = dt.datetime(2026, 10, 16, 2, 0, tzinfo=dt.timezone.utc)
    assert service.today_local(late_utc) == dt.date(2026, 10, 15)


# --- ingest_samples -----------------------------------------------------


def test_a_clean_sync_is_stored():
    result = service.ingest_samples(
        TEST_USER,
        [sample(), sample("spo2", value=97.0)],
        device_id="watch-1",
        now=PINNED_NOW,
    )

    assert result["accepted"] == 2
    assert result["rejected"] == []
    assert result["stored_dates"] == [PINNED_TODAY.isoformat()]
    assert result["metrics_stored"] == ["resting_heart_rate", "spo2"]
    assert result["last_sync_at"] == "2026-10-15T14:00:00Z"

    history = sample_store.get_store().history(TEST_USER)
    assert history["resting_heart_rate"][PINNED_TODAY] == 62.0
    assert sample_store.get_store().device_state(TEST_USER)["device_id"] == "watch-1"


def test_one_bad_sample_does_not_cost_the_user_the_batch():
    result = service.ingest_samples(
        TEST_USER,
        [
            sample("resting_heart_rate", value=62.0),
            sample("resting_heart_rate", days_ago=1, value=0.0),  # dropped contact
            sample("spo2", value=97.0),
        ],
        now=PINNED_NOW,
    )

    assert result["accepted"] == 2
    assert len(result["rejected"]) == 1

    rejection = result["rejected"][0]
    assert rejection["index"] == 1  # lines up with the request array
    assert rejection["reason"] == service.REASON_OUT_OF_RANGE
    assert rejection["expected_range"] == [30.0, 150.0]
    assert rejection["unit"] == "bpm"


def test_an_entirely_invalid_sync_reports_every_rejection():
    result = service.ingest_samples(
        TEST_USER,
        [sample("made_up_metric"), sample("spo2", value=5.0)],
        now=PINNED_NOW,
    )

    assert result["accepted"] == 0
    assert [item["reason"] for item in result["rejected"]] == [
        service.REASON_UNKNOWN_METRIC,
        service.REASON_OUT_OF_RANGE,
    ]
    assert result["stored_dates"] == []


def test_re_syncing_the_same_night_overwrites_rather_than_duplicates():
    """Watches revise overnight metrics once sleep analysis finishes."""
    service.ingest_samples(TEST_USER, [sample(value=62.0)], now=PINNED_NOW)
    service.ingest_samples(TEST_USER, [sample(value=64.0)], now=PINNED_NOW)

    history = sample_store.get_store().history(TEST_USER)
    assert history["resting_heart_rate"] == {PINNED_TODAY: 64.0}


def test_a_repeat_within_one_payload_keeps_the_last_value():
    service.ingest_samples(
        TEST_USER, [sample(value=62.0), sample(value=65.0)], now=PINNED_NOW
    )
    history = sample_store.get_store().history(TEST_USER)
    assert history["resting_heart_rate"][PINNED_TODAY] == 65.0


def test_a_sync_touches_only_its_own_user():
    service.ingest_samples(TEST_USER, [sample(value=62.0)], now=PINNED_NOW)
    service.ingest_samples("someone-else", [sample(value=90.0)], now=PINNED_NOW)

    store = sample_store.get_store()
    assert store.history(TEST_USER)["resting_heart_rate"][PINNED_TODAY] == 62.0
    assert store.history("someone-else")["resting_heart_rate"][PINNED_TODAY] == 90.0


def test_integers_are_accepted_and_stored_as_floats():
    service.ingest_samples(TEST_USER, [sample("steps", value=8000)], now=PINNED_NOW)
    stored = sample_store.get_store().history(TEST_USER)["steps"][PINNED_TODAY]
    assert isinstance(stored, float) and stored == 8000.0


# --- the store itself ---------------------------------------------------


def test_history_is_a_copy_so_callers_cannot_corrupt_the_store():
    service.ingest_samples(TEST_USER, [sample(value=62.0)], now=PINNED_NOW)
    store = sample_store.get_store()

    history = store.history(TEST_USER)
    history["resting_heart_rate"][PINNED_TODAY] = 999.0
    history["invented_metric"] = {}

    assert store.history(TEST_USER)["resting_heart_rate"][PINNED_TODAY] == 62.0
    assert "invented_metric" not in store.history(TEST_USER)


def test_an_unknown_user_has_an_empty_history_not_an_error():
    store = sample_store.get_store()
    assert store.history("nobody") == {}
    assert store.device_state("nobody") == {"device_id": None, "last_sync_at": None}


def test_samples_survive_a_restart(tmp_path):
    path = tmp_path / "store.json"
    sample_store.reset_store(path=path)
    service.ingest_samples(TEST_USER, [sample(value=62.0)], device_id="w1", now=PINNED_NOW)

    # A fresh store object over the same file is what a restart looks like.
    sample_store.reset_store(path=path)
    reloaded = sample_store.get_store()

    assert reloaded.history(TEST_USER)["resting_heart_rate"] == {PINNED_TODAY: 62.0}
    assert reloaded.device_state(TEST_USER)["device_id"] == "w1"
    assert reloaded.device_state(TEST_USER)["last_sync_at"] == PINNED_NOW


def test_the_store_file_is_readable_json(tmp_path):
    path = tmp_path / "store.json"
    sample_store.reset_store(path=path)
    service.ingest_samples(TEST_USER, [sample(value=62.0)], device_id="w1", now=PINNED_NOW)

    payload = json.loads(path.read_text())
    assert payload["version"] == sample_store.STORE_FORMAT_VERSION
    assert payload["users"][TEST_USER]["metrics"]["resting_heart_rate"] == {
        PINNED_TODAY.isoformat(): 62.0
    }
    assert payload["users"][TEST_USER]["device"]["last_sync_at"] == "2026-10-15T14:00:00Z"


def test_a_rejected_sync_still_records_that_the_watch_checked_in(tmp_path):
    """last_sync_at is how the app knows the device is reporting at all."""
    path = tmp_path / "store.json"
    sample_store.reset_store(path=path)
    service.ingest_samples(
        TEST_USER, [sample(value=0.0)], device_id="w1", now=PINNED_NOW
    )

    sample_store.reset_store(path=path)
    state = sample_store.get_store().device_state(TEST_USER)
    assert state["device_id"] == "w1"
    assert state["last_sync_at"] == PINNED_NOW


def test_no_temporary_files_are_left_behind(tmp_path):
    path = tmp_path / "store.json"
    sample_store.reset_store(path=path)
    service.ingest_samples(TEST_USER, [sample(value=62.0)], now=PINNED_NOW)

    assert [item.name for item in tmp_path.iterdir()] == ["store.json"]


def test_a_fresh_store_seeds_the_demo_user(tmp_path):
    sample_store.reset_store(path=tmp_path / "store.json", seed_demo_user=True)
    store = sample_store.get_store()

    assert "demo-user" in store.known_users()
    assert store.history("demo-user")["resting_heart_rate"]
