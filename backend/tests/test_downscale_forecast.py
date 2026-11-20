"""Exercise actual model inference, unit conversion, timing and publication failure."""
from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest
import xarray as xr

torch = pytest.importorskip("torch")
pytest.importorskip("asthma_aware")
from asthma_aware.source_models import SourceModelConfig, build_source_models
from scripts import downscale_forecast as inference


@pytest.fixture
def checkpoint(tmp_path):
    torch.set_num_threads(1)
    config = SourceModelConfig(forecast_channels=5, target_names=("surface_o3_ppb",),
        encoder_channels=(4, 8), forecast_encoder_channels=(4, 8), head_channels=4, time_channels=4)
    model = build_source_models({"o3": config})["o3"]
    value = dict(source="o3", target="surface_o3_ppb", target_units="ppb",
        model_config=asdict(config), model_state=model.state_dict(), epoch=1,
        product="analysis", forecast_skill_validated=False, data_signature="test-training-signature",
        source_config={"predictors": ["o3", "air_temperature", "specific_humidity", "surface_pressure", "pbl_height"],
                       "coarse_shape": [2, 2]},
        normalization={"signature": "test-training-signature", "source": "o3",
            "input_mean": [0] * 5, "input_std": [1] * 5, "target_mean": 30., "target_std": 15.},
        grid={"crs": "EPSG:4326", "width": 8, "height": 8,
              "transform": (0.01, 0., -84.5, 0., -0.01, 33.25)})
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    path = model_dir / "o3_best.pt"
    torch.save(value, path)
    source = tmp_path / "nasa.nc"
    fields = {"o3": (2e-8, "mol mol-1"), "air_temperature": (290, "k"),
              "specific_humidity": (.01, "kg kg-1"), "surface_pressure": (100000, "pa"), "pbl_height": (500, "m")}
    data = xr.Dataset({name: (("time", "lat", "lon"), np.full((2, 2, 2), number, dtype="float32"), {"units": unit})
                       for name, (number, unit) in fields.items()},
        coords={"time": pd.date_range("2026-11-18T09:30", periods=2, freq="h"),
                "lat": [33., 33.25], "lon": [-84.5, -84.25]},
        attrs={"initialization_time": "2026-11-18T09:00", "forecast_run": "20261118_09z"})
    data.to_netcdf(source)
    return source, model_dir, value, data


def test_surface_ozone_uses_ppb_baseline_and_hour_start_features(checkpoint, tmp_path, monkeypatch):
    source, models, _, _ = checkpoint
    seen = []
    original = inference.time_features
    def feature_time(value):
        seen.append(value)
        return original(value)
    monkeypatch.setattr(inference, "time_features", feature_time)
    output = tmp_path / "layers.nc"
    inference.write_layers(source, models, output, sources=["o3"])
    with xr.open_dataset(output) as data:
        assert set(data.data_vars) == {"surface_o3_ppb"}
        assert data.surface_o3_ppb.shape == (2, 8, 8)
        # The zero-residual model must return NASA ozone * 1e9, not a column or normalized value.
        np.testing.assert_allclose(data.surface_o3_ppb, 20., atol=1e-4)
        assert data.surface_o3_ppb.attrs["units"] == "ppb"
        assert data.surface_o3_ppb.attrs["training_product"] == "analysis"
        assert not data.surface_o3_ppb.attrs["forecast_skill_validated"]
        assert len(data.surface_o3_ppb.attrs["checkpoint_sha256"]) == 64
        np.testing.assert_allclose(np.diff(data.lat), -.01)
        assert data.time.values[0] == np.datetime64("2026-11-18T09:30")
    assert seen == [pd.Timestamp("2026-11-18T09:00"), pd.Timestamp("2026-11-18T10:00")]


@pytest.mark.parametrize("problem", ["column_target", "scaler", "missing_weather", "wrong_units", "negative_ozone"])
def test_bad_model_or_data_preserves_existing_artifact(checkpoint, tmp_path, problem):
    source, models, value, data = checkpoint
    if problem == "column_target":
        value["target"] = "total_o3_column"
    elif problem == "scaler":
        value["normalization"]["signature"] = "other-training-run"
    elif problem == "missing_weather":
        data = data.drop_vars("pbl_height")
    elif problem == "wrong_units":
        data.o3.attrs["units"] = "DU"
    else:
        data.o3.values[:] = -2e-8
    torch.save(value, models / "o3_best.pt")
    data.to_netcdf(source)
    output = tmp_path / "layers.nc"
    output.write_bytes(b"previous-good-artifact")
    with pytest.raises((ValueError, KeyError)):
        inference.write_layers(source, models, output, sources=["o3"])
    assert output.read_bytes() == b"previous-good-artifact"
    assert not list(tmp_path.glob(".*.tmp"))


def test_available_model_selection_and_smoke_hour_limit(checkpoint, tmp_path):
    source, models, _, _ = checkpoint
    assert inference.selected_sources(models) == ["o3"]
    assert inference.required_variables(models, ["o3"]) == {
        "o3", "air_temperature", "specific_humidity", "surface_pressure", "pbl_height"}
    output = tmp_path / "layers.nc"
    inference.write_layers(source, models, output, hours=1)
    with xr.open_dataset(output) as data:
        assert data.sizes["time"] == 1
    with pytest.raises(FileNotFoundError):
        inference.selected_sources(models, ["no2"])
