"""Physical map units, grid mapping, and model/source provenance."""
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from app.services.map_fields import MapFields, write_map_fields
from app.services.risk_artifacts import LATITUDES, LONGITUDES, reference_id
from app.services.risk_map import colorize_frame, map_forecast, map_image, LayerUnavailable


@pytest.fixture
def fields(tmp_path):
    times = pd.date_range("2026-11-10T09:30", periods=3, freq="h")
    lat, lon = np.arange(30.5, 35.51, .25), np.arange(-86, -81.24, .25)
    shape = (3, len(lat), len(lon))
    raw = xr.Dataset({name: (("time", "lat", "lon"), np.full(shape, value), {"units": unit})
        for name, value, unit in [("o3", 40e-9, "mol mol-1"), ("no2", 7e-9, "mol mol-1"),
            ("so2", 2e-9, "mol mol-1"), ("pm25", 12., "ug m-3"), ("air_temperature", 270., "K")]},
        coords=dict(time=times, lat=lat, lon=lon),
        attrs=dict(forecast_run="test", initialization_time="2026-11-10T09:00:00Z"))
    raw.pm25[1] = np.nan
    source = tmp_path / "source.nc"
    raw.to_netcdf(source)
    fine_shape = (3, len(LATITUDES), len(LONGITUDES))
    model = xr.Dataset({
        "surface_o3_ppb": (("time", "lat", "lon"), np.full(fine_shape, 55.), {"units": "ppb"}),
        "temperature_c": (("time", "lat", "lon"), np.full(fine_shape, -3.), {"units": "degC"}),
        # Column NO2 must never replace surface NO2.
        "tropospheric_no2_column": (("time", "lat", "lon"), np.full(fine_shape, .001))},
        coords=dict(time=times, lat=LATITUDES, lon=LONGITUDES), attrs=dict(source_sha256=reference_id(source)))
    model_path = tmp_path / "models.nc"
    model.to_netcdf(model_path)
    output = tmp_path / "map.nc"
    digest = write_map_fields(source, model_path, output)
    return MapFields(output, digest), source, model_path, output


def test_physical_units_and_native_vs_model_fields(fields):
    data, source, _, output = fields
    for layer, value in [("no2", 7), ("so2", 2), ("pm25", 12), ("o3", 55), ("temperature", -3)]:
        frame = data.frame(layer, data.times[0])
        assert frame.shape == (420, 408)
        np.testing.assert_allclose(frame, value)
    assert not data.available["pm25"][1]
    assert np.isnan(data.frame("pm25", data.times[1])).all()
    with xr.open_dataset(output) as stored:
        assert stored.no2.dims == ("time", "source_lat", "source_lon")
        assert stored.o3.dims == ("time", "lat", "lon")
    assert "co" not in data.layers and "pm10" not in data.layers
    fallback = output.with_name("native.nc")
    native = MapFields(fallback, write_map_fields(source, None, fallback))
    np.testing.assert_allclose(native.frame("temperature", native.times[0]), -3.15, atol=1e-4)


def test_layer_timestamps_cache_identity_and_independent_availability(fields):
    data, _, _, _ = fields
    # Risk indices include preceding history; physical fields start at issue time.
    times = pd.date_range(data.times[0] - timedelta(hours=2), periods=5, freq="h")
    artifact = SimpleNamespace(times=times, digest="a" * 64, rows=np.zeros(420, int), columns=np.zeros(408, int),
        general_scores=np.full((5, 1, 1), np.nan), metadata=dict(issued_at="2026-11-10T09:00:00Z",
            source_run="test", product="forecast", provisional=True))
    now = pd.Timestamp("2026-11-10T09:00:00Z")
    risk = map_forecast(artifact, now, data)
    ozone = map_forecast(artifact, now, data, "o3")
    pm = map_forecast(artifact, now, data, "pm25")
    assert ozone.frames[0].index == 2 and ozone.frames[0].available
    assert not risk.frames[0].available
    assert not pm.frames[1].available
    assert len({risk.run_id, ozone.run_id, pm.run_id}) == 3
    assert ozone.layer.unit == "ppb"
    assert map_image(artifact, 2, data, "o3").startswith("data:image/png;base64,")
    with pytest.raises(LayerUnavailable):
        map_forecast(artifact, now, data, "co")


def test_fixed_palette_clipping_and_missing_temperature():
    values = np.full((420, 408), -5.)
    values[:, 0] = np.nan
    values[:, 1] = -100
    values[:, 2] = 100
    image = colorize_frame(values, ["#000000", "#ffffff"], -10, 45)
    assert not image[:, 0, 3].any()
    assert (image[:, 1, :3] == 0).all()
    assert (image[:, 2, :3] == 255).all()
    assert (image[:, 3, 3] == 255).all()


def test_source_identity_and_cached_checksum_are_enforced(fields, tmp_path):
    from app.services.map_fields import checked_fields
    _, source, models, output = fields
    info = output.stat()
    with pytest.raises(ValueError, match="checksum"):
        checked_fields(output, info.st_mtime_ns, info.st_size, "0" * 64)
    with xr.open_dataset(models) as data:
        changed = data.load().assign_attrs(source_sha256="0" * 64)
    wrong = tmp_path / "wrong.nc"
    changed.to_netcdf(wrong)
    with pytest.raises(ValueError, match="NASA source"):
        write_map_fields(source, wrong, tmp_path / "invalid.nc")
    assert not (tmp_path / "invalid.nc").exists()


def test_private_gcs_fields_download_once_and_rebase_publication(fields, monkeypatch, tmp_path):
    from unittest.mock import Mock
    from uuid import uuid4
    from app.services import gcs_forecast as gcs
    from app.services.map_fields import resolve_map_fields
    from app.services.geos_dashboard import PublishedForecast
    from app.config import Settings
    import tempfile
    data, _, _, output = fields
    run = PublishedForecast(source_run="test", issued_at=data.issued_at,
        times=list(data.times.to_pydatetime()), latitudes=[33.75], longitudes=[-84.5],
        pm25=[[12.] for _ in data.times], map_artifact="local.nc", map_artifact_sha256=data.digest)
    publication_id = uuid4()
    key = f"published/{publication_id}/map-fields.nc"
    blob = Mock()
    blob.download_to_filename.side_effect = lambda target, **kwargs: target.write_bytes(output.read_bytes())
    metadata_blob = Mock()
    metadata_blob.download_as_bytes.return_value = run.model_dump_json().encode()
    bucket = Mock()
    bucket.blob.side_effect = lambda name: blob if name == key else metadata_blob
    monkeypatch.setattr(gcs, "get_bucket", lambda name: bucket)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "cache"))
    gcs.read_artifact.cache_clear()
    remote = gcs.read_artifact("test", publication_id)
    assert remote.map_artifact == key
    settings = Settings(forecast_gcs_bucket="test")
    first = resolve_map_fields(settings, remote)
    assert first is resolve_map_fields(settings, remote)
    blob.download_to_filename.assert_called_once()
    np.testing.assert_allclose(first.frame("no2", first.times[0]), 7)


def test_frame_reader_does_not_reopen_xarray_or_change_values_by_request_order(fields, monkeypatch):
    data, _, _, _ = fields
    expected = {layer: data.frame(layer, data.times[2]).copy() for layer in data.layers}
    monkeypatch.setattr(xr, "open_dataset", lambda *args, **kwargs: pytest.fail("Frame reads must slice NetCDF directly"))
    for layer in ["pm25", "no2", "so2", "o3", "temperature", "pm25", "so2", "no2"]:
        np.testing.assert_array_equal(data.frame(layer, data.times[2]), expected[layer])
    assert np.isnan(data.frame("pm25", data.times[1])).all()
    assert data.layers["no2"]["maximum"] == 10
    assert data.layers["so2"]["maximum"] == 2
