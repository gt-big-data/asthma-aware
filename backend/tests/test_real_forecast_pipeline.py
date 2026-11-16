"""Real serialization/publication contracts using explicit offline NASA fixtures."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from forecast_job.storage import Conflict, MemoryStore
from forecast_pipeline.catalog import ForecastCatalog, create_immutable, publish_manifest, reference
from forecast_pipeline.contracts import CatalogError, ProductUnavailable, digest, encode, validate_manifest
from forecast_pipeline.ml_handoff import publish_ml_outputs
from forecast_pipeline.pipeline import ingest, publish_raw, validate_raw
from forecast_pipeline.storage import LocalArtifactStore
from ingestion.geos_cf import fetch_forecast as nasa
from tests.geos_cf_fakes import INIT, fake_remote

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


@pytest.fixture
def raw(tmp_path, monkeypatch):
    monkeypatch.setattr(nasa, "open_remote", lambda url: fake_remote(next(c for c in nasa.COLLECTIONS if c in url), hours=48))
    monkeypatch.setattr(nasa, "remote_call", lambda fn, *args, **kwargs: fn(*args))
    ds = nasa.build_forecast(INIT, nasa.bbox_area(33.45, 34.05, -84.80, -83.95), slice(0, 48))
    nasa.validate_forecast(ds, 48)
    metadata = nasa.build_metadata(ds, INIT, nasa.bbox_area(33.45, 34.05, -84.80, -83.95), slice(0, 48), NOW)
    path, _ = nasa.write_outputs(ds, metadata, tmp_path / "raw")
    return path, metadata


def test_raw_netcdf_validation_and_duplicate_safe_publication(raw):
    path, metadata = raw
    assert validate_raw(path, now=NOW) == metadata
    store = MemoryStore()
    first = publish_raw(store, path, now=NOW)
    assert first["promoted"] is True
    assert first["manifest"]["product"] == "raw"
    assert first["manifest"]["bounds"]["min_lat"] == metadata["grid_extent"]["min_lat"] - .125
    assert first["manifest"]["bounds"]["max_lon"] == metadata["grid_extent"]["max_lon"] + .125
    assert "score" not in json.dumps(first)
    size = len(store.objects)
    assert publish_raw(store, path, now=NOW)["promoted"] is False
    assert len(store.objects) == size
    status = ForecastCatalog(store).availability(NOW)
    assert status["raw"]["status"] == "available"
    assert status["processed"]["status"] == "pending"
    with pytest.raises(ProductUnavailable):
        ForecastCatalog(store).delivery(first["manifest"]["files"][0]["name"], NOW)


def test_raw_metadata_tamper_missing_values_and_staleness(raw, tmp_path):
    path, _ = raw
    with pytest.raises(CatalogError, match="older"):
        validate_raw(path, now=NOW + timedelta(days=3))
    metadata_path = path.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text())
    metadata["source_urls"] = ["https://untrusted.example/data"]
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(CatalogError, match="pinned"):
        validate_raw(path, now=NOW)


@pytest.mark.parametrize("field,value", [("units", {"pm25": "wrong"}), ("variables", ["pm25"]),
    ("valid_time_start", "2026-10-15T10:30:00Z"), ("valid_time_end", "2026-10-15T11:30:00Z")])
def test_sidecar_contract_cannot_disagree_with_netcdf(raw, field, value):
    path, metadata = raw
    metadata[field] = value
    path.with_suffix(".metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(CatalogError):
        validate_raw(path, now=NOW)


@pytest.mark.parametrize("bad", [np.nan, -1.0])
def test_raw_missing_or_negative_pollutant_is_never_filled(raw, bad):
    path, _ = raw
    with xr.open_dataset(path) as opened:
        ds = opened.load()
    ds["pm25"][0, 0, 0] = bad
    ds.to_netcdf(path, format="NETCDF4")
    with pytest.raises((CatalogError, nasa.ValidationError)):
        validate_raw(path, now=NOW)


def test_raw_partial_upload_never_promotes(raw):
    path, _ = raw
    class FailingStore(MemoryStore):
        def write(self, name, data, expected=0):
            if name.rsplit("/", 1)[-1].startswith("metadata-"):
                raise OSError("test upload failure")
            return super().write(name, data, expected)
    store = FailingStore()
    with pytest.raises(OSError):
        publish_raw(store, path, now=NOW)
    assert "raw/current.json" not in store.objects


def test_interrupted_raw_upload_recovers_after_redownload_changes_serialization(raw):
    path, metadata = raw
    class FailOnceStore(MemoryStore):
        fail = True
        def write(self, name, data, expected=0):
            if self.fail and name.rsplit("/", 1)[-1].startswith("metadata-"):
                self.fail = False
                raise OSError("test interrupted first publication")
            return super().write(name, data, expected)
    store = FailOnceStore()
    with pytest.raises(OSError):
        publish_raw(store, path, now=NOW)
    assert "raw/current.json" not in store.objects
    first_file = next(name for name in store.objects if name.endswith(".nc"))
    first_data = store.read(first_file).data
    # Model-free, test-only redownload simulation: native scientific values are
    # identical but genuine NetCDF metadata/acquisition time is newer.
    with xr.open_dataset(path) as opened:
        ds = opened.load()
    metadata["downloaded_at"] = (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    nasa.write_outputs(ds, metadata, path.parent)
    assert path.read_bytes() != first_data
    result = publish_raw(store, path, now=NOW + timedelta(minutes=1))
    assert result["promoted"] is True
    selected = next(ref for ref in result["manifest"]["files"] if ref["content_type"] == "application/x-netcdf")
    assert selected["name"] != first_file
    assert len([name for name in store.objects if name.endswith(".nc")]) == 2
    assert ForecastCatalog(store).availability(NOW)["raw"]["status"] == "available"


def test_concurrent_same_science_redownload_uses_winning_complete_manifest(raw, tmp_path):
    path, metadata = raw
    with xr.open_dataset(path) as opened:
        ds = opened.load()
    metadata = deepcopy(metadata)
    metadata["downloaded_at"] = (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    second_path, _ = nasa.write_outputs(ds, metadata, tmp_path / "concurrent-download")
    class RacingStore(MemoryStore):
        race = True
        def write(self, name, data, expected=0):
            if self.race and name.endswith("manifest.json"):
                self.race = False
                publish_raw(self, second_path, now=NOW + timedelta(minutes=1))
            return super().write(name, data, expected)
    store = RacingStore()
    result = publish_raw(store, path, now=NOW)
    assert result["promoted"] is False
    assert ForecastCatalog(store).metadata("raw", NOW) == result["manifest"]


def test_local_store_generation_pinning_and_atomic_cas(tmp_path):
    store = LocalArtifactStore(tmp_path)
    first = store.write("raw/test.json", b"first")
    second = store.write("raw/test.json", b"second", expected=first.generation)
    assert store.read("raw/test.json").generation == second.generation
    assert store.read("raw/test.json", first.generation).data == b"first"
    with pytest.raises(Conflict):
        store.write("raw/test.json", b"older", expected=first.generation)
    with pytest.raises(CatalogError):
        store.read("../secrets")


def output_manifest(raw_store, raw_result, root):
    raw_manifest = raw_result["manifest"]
    raw_name = f"raw/{raw_manifest['run_id']}/{raw_manifest['artifact_version']}/manifest.json"
    raw_ref = reference(raw_name, raw_store.read(raw_name))
    # Arbitrary test output bytes exist only inside this automated test. The
    # production handoff never generates any output when ML files are absent.
    data = b"test-only supplied ML tile"
    path = root / "tile.mvt"
    path.write_bytes(data)
    return {"schema_version": "asthma-ml-output-v1", "product": "processed", "source": "NASA GEOS-CF v2",
        "run_id": raw_manifest["run_id"], "artifact_version": "model-v1",
        "initialized_at": raw_manifest["initialized_at"], "valid_from": raw_manifest["valid_from"],
        "valid_until": raw_manifest["valid_until"], "bounds": raw_manifest["bounds"],
        "variables": {"temperature_c": "degC"}, "audience": "environmental-public", "contains_user_data": False,
        "model": {"name": "test-fixture-model", "version": "1", "input_contract": "nasa-geos-cf-raw-v1"},
        "input_raw_manifest": raw_ref,
        "files": [{"name": f"processed/{raw_manifest['run_id']}/model-v1/tile.mvt", "relative_path": "tile.mvt",
                   "sha256": digest(data), "size_bytes": len(data), "content_type": "application/vnd.mapbox-vector-tile"}]}


def test_actual_supplied_ml_files_staged_and_delivered_with_bucket_specific_generations(raw, tmp_path):
    source, staging, delivery = MemoryStore(), MemoryStore(), MemoryStore()
    first = publish_raw(source, raw[0], now=NOW)
    declaration = output_manifest(source, first, tmp_path)
    delivery.write("processed/unrelated/test.json", b"{}")
    delivery.write("processed/unrelated/second.json", b"{}")
    result = publish_ml_outputs(declaration, tmp_path, source, staging, delivery)
    assert result["promoted"]
    name = declaration["files"][0]["name"]
    assert staging.read(name).generation != delivery.read(name).generation
    assert ForecastCatalog(delivery).delivery(name, NOW)[0] == b"test-only supplied ML tile"
    assert ForecastCatalog(delivery).availability(NOW)["ml_status"] == "published"
    assert publish_ml_outputs(declaration, tmp_path, source, staging, delivery)["promoted"] is False


@pytest.mark.parametrize("bad", ["checksum", "escape", "personal", "wrong_run", "missing_file", "outside_bounds"])
def test_ml_handoff_rejects_incomplete_unlinked_private_or_escaping_outputs(raw, tmp_path, bad):
    source, staging, delivery = MemoryStore(), MemoryStore(), MemoryStore()
    result = publish_raw(source, raw[0], now=NOW)
    declaration = output_manifest(source, result, tmp_path)
    if bad == "checksum":
        declaration["files"][0]["sha256"] = "0" * 64
    elif bad == "escape":
        declaration["files"][0]["relative_path"] = "../private-file"
    elif bad == "personal":
        declaration["contains_user_data"] = True
    elif bad == "wrong_run":
        declaration["initialized_at"] = "2026-10-14T09:00:00Z"
        declaration["run_id"] = "20261014_09z"
        declaration["files"][0]["name"] = declaration["files"][0]["name"].replace("20261015_09z", "20261014_09z")
    elif bad == "outside_bounds":
        declaration["bounds"]["min_lat"] -= .01
    else:
        (tmp_path / "tile.mvt").unlink()
    with pytest.raises((CatalogError, KeyError)):
        publish_ml_outputs(declaration, tmp_path, source, staging, delivery)
    assert not staging.objects and not delivery.objects


def test_delivery_failure_keeps_previous_pointer(raw, tmp_path):
    source, staging, delivery = MemoryStore(), MemoryStore(), MemoryStore()
    result = publish_raw(source, raw[0], now=NOW)
    declaration = output_manifest(source, result, tmp_path)
    class FailingStore(MemoryStore):
        def write(self, name, data, expected=0):
            raise OSError("delivery unavailable")
    broken = FailingStore()
    with pytest.raises(OSError):
        publish_ml_outputs(declaration, tmp_path, source, staging, broken)
    assert "processed/current.json" not in broken.objects


def test_ingestion_cache_reuses_valid_files_without_field_download(raw, monkeypatch):
    path, _ = raw
    monkeypatch.setattr(nasa, "resolve_forecast_run", lambda **kwargs: INIT)
    monkeypatch.setattr(nasa, "build_forecast", lambda *a, **k: pytest.fail("cached native fields were redownloaded"))
    returned, _ = ingest(path.parent, hours=48, now=NOW)
    assert returned == path


def test_ephemeral_task_reuses_durable_product_without_nasa_field_download(raw, tmp_path, monkeypatch):
    source = MemoryStore()
    publish_raw(source, raw[0], now=NOW)
    monkeypatch.setattr(nasa, "resolve_forecast_run", lambda **kwargs: INIT)
    monkeypatch.setattr(nasa, "build_forecast", lambda *a, **k: pytest.fail("durable native fields were redownloaded"))
    path, metadata = ingest(tmp_path / "new-task", hours=48, now=NOW, store=source)
    assert path.exists() and metadata["forecast_run"] == "20261015_09z"
