import numpy as np
import pytest
import xarray as xr

from ingestion.geos_cf.fetch_forecast import ValidationError, canonical_forecast_times
from ingestion.geos_cf.fetch_forecast import open_remote
from ingestion.geos_cf import fetch_forecast as nasa


def test_lazy_remote_time_overflow_uses_bounded_retries(monkeypatch):
    calls = []
    def read(*args, **kwargs):
        calls.append(None)
        if len(calls) < 3:
            raise OverflowError("remote time overflow")
        return "validated data"
    monkeypatch.setattr(nasa, "run_with_timeout", read)
    monkeypatch.setattr(nasa.time, "sleep", lambda seconds: None)
    assert nasa.remote_call(lambda: None, what="NASA test", retries=3) == "validated data"
    assert len(calls) == 3


def test_repeated_lazy_remote_overflow_still_fails(monkeypatch):
    calls = []
    def read(*args, **kwargs):
        calls.append(None)
        raise OverflowError("remote time overflow")
    monkeypatch.setattr(nasa, "run_with_timeout", read)
    monkeypatch.setattr(nasa.time, "sleep", lambda seconds: None)
    with pytest.raises(OverflowError):
        nasa.remote_call(lambda: None, what="NASA test", retries=3)
    assert len(calls) == 3


def test_overflow_from_remote_coordinate_transfer_is_retryable(monkeypatch):
    def failed_transfer(*args, **kwargs):
        raise OverflowError("time values outside range of 64 bit signed integers")

    monkeypatch.setattr(xr, "open_dataset", failed_transfer)
    with pytest.raises(OSError, match="undecodable time coordinate") as error:
        open_remote("https://example.test/nasa")
    assert isinstance(error.value.__cause__, OverflowError)


def test_year_one_nasa_epoch_decodes_without_nanosecond_overflow(tmp_path):
    path = tmp_path / "nasa.nc"
    xr.Dataset(coords={"time": ("time", [739940.3958333334], {
        "units": "days since 1-1-1 00:00:0.0",
    })}).to_netcdf(path)
    with open_remote(str(path)) as dataset:
        np.testing.assert_array_equal(
            canonical_forecast_times(dataset.time.values),
            np.array(["2026-11-19T09:30"], dtype="datetime64[ns]"),
        )


def test_netcdf_decoder_rounding_preserves_the_exact_nasa_hour():
    times = np.array(["2026-11-19T09:30", "2026-11-19T10:30"], dtype="datetime64[ns]")
    decoded = times + np.array([3000, -3000], dtype="timedelta64[ns]")
    np.testing.assert_array_equal(canonical_forecast_times(decoded), times)


@pytest.mark.parametrize("timestamp", [
    "2026-11-19T09:30:00.001", "2026-11-19T09:31", "NaT",
])
def test_real_timestamp_errors_are_rejected(timestamp):
    with pytest.raises(ValidationError):
        canonical_forecast_times(np.array([timestamp], dtype="datetime64[ns]"))
