"""True fine-cell scoring, nearest-cell copying, and consistent calibration."""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from app.services import fine_risk as fine
from app.services.personalization_model import exposure, general_risk
from app.services.risk_artifacts import FINE_PREFIX, LATITUDES, LONGITUDES, RiskArtifact

PIPELINE = FINE_PREFIX + "a" * 64


def test_surface_inputs_copy_native_cells_but_preserve_learned_ozone(tmp_path):
    times = pd.date_range("2026-11-18T09:30", periods=2, freq="h")
    lat, lon = np.arange(30.25, 35.01, .25), np.arange(-85.5, -80.74, .25)
    native = np.arange(len(lat) * len(lon), dtype="float32").reshape(len(lat), len(lon)) + 1
    raw = xr.Dataset({name: (("time", "lat", "lon"), np.repeat(native[None], 2, axis=0) * (1 if name == "pm25" else 1e-9),
        {"units": "ug m-3" if name == "pm25" else "mol mol-1"}) for name in fine.CHANNELS},
        coords={"time": times, "lat": lat, "lon": lon}, attrs={"product": "analysis"})
    source = tmp_path / "raw.nc"
    raw.to_netcdf(source)
    learned = np.broadcast_to(np.arange(408, dtype="float32") / 10 + 10, (2, 420, 408))
    model = xr.Dataset({"surface_o3_ppb": (("time", "lat", "lon"), learned,
        {"units": "ppb", "checkpoint_sha256": "a" * 64})},
        coords={"time": times, "lat": LATITUDES, "lon": LONGITUDES}, attrs={"source_sha256": fine.reference_id(source)})
    layers, output = tmp_path / "layers.nc", tmp_path / "surface.nc"
    model.to_netcdf(layers)
    fine.write_surface(source, layers, output)
    with xr.open_dataset(output) as data:
        assert data.attrs["preprocessing"] == PIPELINE
        # These nearby fine cells are inside one native cell, but ozone differs.
        assert data.pm25.values[0, 100, 101] == data.pm25.values[0, 100, 102]
        assert data.o3.values[0, 100, 101] != data.o3.values[0, 100, 102]
        np.testing.assert_allclose(data.no2, data.pm25, rtol=1e-6)
        np.testing.assert_allclose(data.o3, learned)
    shifted = model.assign_coords(time=model.time + np.timedelta64(1, "h"))
    shifted.to_netcdf(layers)
    with pytest.raises(ValueError, match="timeline"):
        fine.write_surface(source, layers, tmp_path / "bad.nc")


@pytest.fixture
def small_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(fine, "LATITUDES", LATITUDES[:3])
    monkeypatch.setattr(fine, "LONGITUDES", LONGITUDES[:4])
    times = pd.date_range("2026-11-01T00:30", periods=120, freq="h")
    rng = np.random.default_rng(17)
    values = rng.uniform(1, 40, (120, 4, 3, 4)).astype("float32")
    data = xr.Dataset({name: (("time", "lat", "lon"), values[:, i],
        {"units": "ug m-3" if name == "pm25" else "ppb"}) for i, name in enumerate(fine.CHANNELS)},
        coords={"time": times, "lat": fine.LATITUDES, "lon": fine.LONGITUDES},
        attrs={"preprocessing": PIPELINE, "product": "analysis"})
    path = tmp_path / "fine.nc"
    data.to_netcdf(path)
    return path, data, values


def test_tiled_exposures_and_score_match_full_calculation(small_sources, tmp_path):
    path, _, values = small_sources
    expected = exposure(values)
    with fine.FineSources([path]) as sources:
        tiles = list(sources.tiles(height=1))
        actual = np.concatenate([tile for _, tile in tiles], axis=2)
    np.testing.assert_allclose(actual, expected, equal_nan=True, rtol=1e-6)
    reference = tmp_path / "reference.npz"
    fine.fit_fine_reference([path], reference, provisional=True)
    result = tmp_path / "risk.npz"
    fine.build_fine_artifact([path], reference, result, issued_at="2026-11-01T00:00Z", source_run="test", product="analysis")
    ref, _ = fine.load_reference(reference)
    with np.load(result, allow_pickle=False) as saved:
        np.testing.assert_allclose(saved["E"], expected[78:], equal_nan=True, rtol=1e-6)
        np.testing.assert_allclose(saved["general_scores"], general_risk(actual, ref).score[78:], equal_nan=True, atol=1e-5)
        np.testing.assert_array_equal(saved["source_rows"], np.arange(3))


def test_gaps_and_overlaps_are_handled_before_exposure(small_sources, tmp_path):
    path, data, values = small_sources
    override = data.isel(time=[90]).copy(deep=True)
    override.o3.values[:] = 100
    extra = tmp_path / "override.nc"
    override.to_netcdf(extra)
    gapped = tmp_path / "gapped.nc"
    data.drop_isel(time=80).to_netcdf(gapped)
    values[80] = np.nan
    values[90, 0] = 100
    with fine.FineSources([gapped, extra]) as sources:
        result = next(sources.tiles())[1]
        np.testing.assert_allclose(result, exposure(values), equal_nan=True, rtol=1e-6)


def test_reference_rejects_forecasts_and_mismatched_checkpoint(small_sources, tmp_path):
    path, data, _ = small_sources
    reference = tmp_path / "ref.npz"
    fine.fit_fine_reference([path], reference, provisional=True)
    data.attrs["product"] = "forecast"
    changed = tmp_path / "changed.nc"
    data.to_netcdf(changed)
    with pytest.raises(ValueError, match="analysis"):
        fine.fit_fine_reference([changed], tmp_path / "bad.npz", provisional=True)
    data.attrs["preprocessing"] = FINE_PREFIX + "b" * 64
    data.to_netcdf(changed)
    with pytest.raises(ValueError, match="preprocessing mismatch"):
        fine.build_fine_artifact([changed], reference, tmp_path / "bad.npz", issued_at="2026-11-01", source_run="test", product="forecast")


def test_fine_artifact_point_dashboard_and_map_share_individual_cells(tmp_path):
    from app.services.personalization_model import ReferenceStats, FEATURE_VERSION, MODEL_VERSION
    from app.services.risk_dashboard import risk_dashboard
    from app.services.risk_map import frame_rgba
    E = np.ones((2, 4, 420, 408), dtype="float32") * 10
    E[:, 0, :, 101] = 30
    E[:, 0, :, 102] = 60
    ref = ReferenceStats(np.ones(4), np.ones(4), np.zeros(4), np.linspace(0, 1, 10000))
    scores = general_risk(E, ref).score.astype("float32")
    path = tmp_path / "fine.npz"
    fine.save_npz(path, E=E, general_scores=scores, times=pd.date_range("2026-11-18T09:30", periods=2, freq="h").values.astype("datetime64[ns]").astype(np.int64),
        source_rows=np.arange(420), source_columns=np.arange(408), mean=ref.mean, std=ref.std,
        clean=ref.clean, eta_sorted=ref.eta_sorted, metadata=json.dumps(dict(preprocessing=PIPELINE,
            product="forecast", source_run="test", issued_at="2026-11-18T09:00Z", reference_id="test",
            model_version=MODEL_VERSION, feature_version=FEATURE_VERSION, provisional=True, storage="mmap-npz-v1")))
    artifact = RiskArtifact(path)
    assert isinstance(artifact.E, np.memmap)
    left, right = artifact.point(float(LATITUDES[100]), float(LONGITUDES[101])), artifact.point(float(LATITUDES[100]), float(LONGITUDES[102]))
    assert left[0, 0, 0, 0] != right[0, 0, 0, 0]
    dashboard = risk_dashboard(artifact, float(LATITUDES[100]), float(LONGITUDES[101]), datetime(2026, 11, 18, 9, tzinfo=timezone.utc))
    assert dashboard.hourly[0].general_score == round(float(scores[0, 100, 101]))
    rgba = frame_rgba(artifact, 0)
    assert np.any(rgba[:, 101] != rgba[:, 102])
