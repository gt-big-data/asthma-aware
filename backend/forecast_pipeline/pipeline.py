"""Real NASA acquisition, validation and raw publication. No ML computation."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import hashlib
from pathlib import Path

from forecast_pipeline.contracts import (CatalogError, RAW_SCHEMA, digest, encode,
    timestamp, validate_manifest, MAX_OBJECT_BYTES)
from forecast_pipeline.catalog import create_immutable, publish_manifest, reference, read_checked, read_json

ATLANTA_BBOX = {"min_lat": 33.45, "max_lat": 34.05, "min_lon": -84.80, "max_lon": -83.95}


@contextmanager
def request_limits():
    """Apply limits inside the dedicated batch process, restoring module state."""
    from ingestion.geos_cf import fetch_forecast as nasa
    original_call = nasa.remote_call
    original_timeout, original_backoff = nasa.REQUEST_TIMEOUT_SECONDS, nasa.RETRY_BACKOFF_SECONDS
    def bounded_call(fn, *args, **kwargs):
        kwargs["retries"] = min(kwargs.get("retries", 2), 2)
        return original_call(fn, *args, **kwargs)
    nasa.remote_call, nasa.REQUEST_TIMEOUT_SECONDS, nasa.RETRY_BACKOFF_SECONDS = bounded_call, 20, 2
    try:
        yield
    finally:
        nasa.remote_call = original_call
        nasa.REQUEST_TIMEOUT_SECONDS, nasa.RETRY_BACKOFF_SECONDS = original_timeout, original_backoff


def validate_raw(path, *, now=None):
    """Validate a bounded actual ingestion NetCDF against its source sidecar."""
    import numpy as np
    import pandas as pd
    import xarray as xr
    from ingestion.geos_cf import fetch_forecast as nasa
    path = Path(path)
    if not 0 < path.stat().st_size <= MAX_OBJECT_BYTES:
        raise CatalogError("Raw forecast exceeds artifact size limit")
    sidecar = path.with_suffix(".metadata.json")
    if sidecar.stat().st_size > 1024 * 1024:
        raise CatalogError("Raw source metadata exceeds size limit")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    if metadata.get("source") != nasa.SOURCE or metadata.get("source_version") != "v2":
        raise CatalogError("Only actual NASA GEOS-CF v2 ingestion products are accepted")
    init = pd.Timestamp(metadata["initialization_time"]).tz_convert("UTC").tz_localize(None)
    if metadata["forecast_run"] != nasa.run_id_for(init):
        raise CatalogError("Raw forecast initialization and identity disagree")
    expected_urls = [nasa.collection_url(c, metadata["forecast_run"]) for c in nasa.COLLECTIONS]
    if metadata.get("source_urls") != expected_urls:
        raise CatalogError("Expected pinned NASA URLs for all required collections")
    with xr.open_dataset(path) as ds:
        if set(ds.sizes) != {"time", "lat", "lon"} or ds.sizes["time"] > 120 or ds.sizes["lat"] * ds.sizes["lon"] > 1000:
            raise CatalogError("Raw forecast dimensions exceed bounded ingestion")
        nasa.validate_forecast(ds, metadata["forecast_hours"])
        expected_names = {spec.name for spec in nasa.VARIABLES}
        if set(ds.data_vars) != expected_names or set(metadata.get("variables", [])) != expected_names or len(metadata.get("variables", [])) != len(expected_names):
            raise CatalogError("Raw variable names do not match the complete NASA input contract")
        if metadata.get("units") != {name: ds[name].attrs["units"] for name in ds.data_vars}:
            raise CatalogError("Sidecar units differ from validated NetCDF units")
        if metadata.get("variable_source_names") != {spec.name: spec.source_name for spec in nasa.VARIABLES} or metadata.get("variable_source_collections") != {spec.name: spec.collection for spec in nasa.VARIABLES}:
            raise CatalogError("Sidecar source variables do not match NASA collections")
        for coordinate, lower, upper in (("lat", -90, 90), ("lon", -180, 180)):
            values = ds[coordinate].values
            if not np.isfinite(values).all() or (values < lower).any() or (values > upper).any() or (np.diff(values) <= 0).any():
                raise CatalogError("Raw coordinates must be finite ordered geographic centers")
            if len(values) > 1 and not np.allclose(np.diff(values), .25):
                raise CatalogError("Expected native 0.25 degree coordinates")
        for spec in nasa.VARIABLES:
            if not np.isfinite(ds[spec.name].values).all():
                raise CatalogError("Missing or nonfinite native values cannot be fabricated or filled")
            if spec.name in {"pm25", "pm10", "no2", "o3", "co"} and (ds[spec.name].values < 0).any():
                raise CatalogError("Negative surface pollution cannot be filled or clamped")
        times = [pd.Timestamp(t).isoformat() + "Z" for t in ds.time.values]
        if metadata.get("valid_times") != times or not times:
            raise CatalogError("Raw forecast time axis does not match its source metadata")
        if metadata.get("valid_time_start") != times[0] or metadata.get("valid_time_end") != times[-1]:
            raise CatalogError("Raw sidecar validity endpoints do not match the time axis")
        lead = slice(*metadata["lead_hour_range"])
        if lead.start < 0 or lead.stop > 120 or lead.stop - lead.start != len(times):
            raise CatalogError("Raw forecast lead range is invalid")
        if pd.Timestamp(ds.time.values[0]) != nasa.first_valid_time(init, lead):
            raise CatalogError("NASA midpoint timestamps do not match the pinned run")
        expected_extent = {"min_lat": float(ds.lat.min()), "max_lat": float(ds.lat.max()),
            "min_lon": float(ds.lon.min()), "max_lon": float(ds.lon.max()),
            "n_lat": ds.sizes["lat"], "n_lon": ds.sizes["lon"]}
        if metadata.get("grid_extent") != expected_extent:
            raise CatalogError("Raw geographic extent does not match metadata")
    now = now or datetime.now(timezone.utc)
    initialized = timestamp(metadata["initialization_time"])
    acquired = timestamp(metadata["downloaded_at"])
    if acquired < initialized or acquired > now + timedelta(minutes=5):
        raise CatalogError("NASA acquisition timestamp is inconsistent")
    requested = metadata["region_bbox"]
    if set(requested) != {"min_lat", "max_lat", "min_lon", "max_lon"}:
        raise CatalogError("Invalid requested geographic bounds")
    extent = metadata["grid_extent"]
    if not (extent["min_lat"] - .125 <= requested["min_lat"] <= requested["max_lat"] <= extent["max_lat"] + .125
            and extent["min_lon"] - .125 <= requested["min_lon"] <= requested["max_lon"] <= extent["max_lon"] + .125):
        raise CatalogError("Requested geography is not covered by the native grid")
    if initialized > now or now - initialized > timedelta(hours=48):
        raise CatalogError("NASA initialization is future-dated or older than 48 hours")
    return metadata


def request_version(bbox, hours):
    from ingestion.geos_cf import fetch_forecast as nasa
    return digest(encode({"bbox": bbox, "lead": [0, hours],
        "variables": sorted(spec.name for spec in nasa.VARIABLES)}))[:24]


def scientific_digest(path):
    """Ignore NetCDF serialization/acquisition-time differences on redelivery."""
    import numpy as np
    import xarray as xr
    state = hashlib.sha256()
    with xr.open_dataset(path) as ds:
        for name in sorted(set(ds.data_vars) | {"time", "lat", "lon"}):
            value = ds[name]
            state.update(encode({"name": name, "dims": list(value.dims), "units": value.attrs.get("units")}))
            array = value.values
            if np.issubdtype(array.dtype, np.datetime64) or np.issubdtype(array.dtype, np.timedelta64):
                state.update(array.astype("int64").astype("<i8").tobytes())
            else:
                state.update(array.astype("<f8").tobytes())
    return state.hexdigest()


def ingest(output_dir, *, bbox=None, hours=48, run_date=None, now=None, store=None):
    from ingestion.geos_cf import fetch_forecast as nasa
    bbox = bbox or ATLANTA_BBOX
    if type(hours) is not int or not 1 <= hours <= 120:
        raise CatalogError("Forecast hours must be 1..120")
    area = nasa.bbox_area(bbox["min_lat"], bbox["max_lat"], bbox["min_lon"], bbox["max_lon"])
    estimated_cells = (int((bbox["max_lat"] - bbox["min_lat"]) / .25) + 2) * (int((bbox["max_lon"] - bbox["min_lon"]) / .25) + 2)
    if estimated_cells > 1000:
        raise CatalogError("Requested region exceeds the ingestion resource bound")
    with request_limits():
        init = nasa.resolve_forecast_run(run_date=run_date, lookback_days=1)
        run_id = nasa.run_id_for(init)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / (nasa.output_stem(area.name, run_id, slice(0, hours)) + ".nc")
        if path.exists():
            try:
                metadata = validate_raw(path, now=now)
                if metadata["region_bbox"] == bbox and metadata["lead_hour_range"] == [0, hours]:
                    return path, metadata
            except (ValueError, OSError, KeyError):
                pass
        # A new ephemeral Cloud Run task can reuse the prior durable product.
        # Discovery still verifies the latest NASA run; no field download repeats.
        if store is not None:
            name = f"raw/{run_id}/{request_version(bbox, hours)}/manifest.json"
            try:
                manifest = validate_manifest(read_json(store.read(name)), "raw")
                for ref in manifest["files"]:
                    suffix = ".nc" if ref["content_type"] == "application/x-netcdf" else ".metadata.json"
                    target = path.with_suffix(suffix)
                    data = read_checked(store, ref).data
                    temporary = target.with_suffix(target.suffix + ".tmp")
                    temporary.write_bytes(data)
                    temporary.replace(target)
                return path, validate_raw(path, now=now)
            except KeyError:
                pass
        ds = nasa.build_forecast(init, area, slice(0, hours))
        nasa.validate_forecast(ds, hours)
        metadata = nasa.build_metadata(ds, init, area, slice(0, hours), datetime.now(timezone.utc))
        path, _ = nasa.write_outputs(ds, metadata, output_dir)
    return Path(path), validate_raw(path, now=now)


def publish_raw(store, path, metadata=None, *, now=None):
    """Publish immutable raw bytes/metadata then conditionally promote manifest."""
    path = Path(path)
    verified = validate_raw(path, now=now)
    if metadata is not None and verified != metadata:
        raise CatalogError("Supplied raw metadata differs from the validated file")
    metadata = verified
    request = {"bbox": metadata["region_bbox"], "lead": metadata["lead_hour_range"],
               "variables": sorted(metadata["variables"])}
    version = digest(encode(request))[:24]
    prefix = f"raw/{metadata['forecast_run']}/{version}/"
    data_sha = scientific_digest(path)
    try:
        previous = validate_manifest(read_json(store.read(prefix + "manifest.json")), "raw")
        if previous.get("data_sha256") != data_sha:
            raise CatalogError("NASA run/request identity was reused with different scientific data")
        return {"promoted": publish_manifest(store, previous), "manifest": previous}
    except KeyError:
        pass
    files = []
    for stem, suffix, data, mime in (("forecast", ".nc", path.read_bytes(), "application/x-netcdf"),
                                    ("metadata", ".json", encode(metadata), "application/json")):
        # A failed prior task may have uploaded an orphan with a different
        # serialization/acquisition timestamp. Byte-addressed names let another
        # complete attempt recover without overwriting or trusting that orphan.
        name = f"{stem}-{digest(data)}{suffix}"
        obj = create_immutable(store, prefix + name, data, mime)
        files.append(reference(prefix + name, obj, mime))
    first = timestamp(metadata["valid_time_start"]) - timedelta(minutes=30)
    end = timestamp(metadata["valid_time_end"]) + timedelta(minutes=30)
    extent = metadata["grid_extent"]
    manifest = {"schema_version": RAW_SCHEMA, "product": "raw", "source": "NASA GEOS-CF v2",
        "run_id": metadata["forecast_run"], "artifact_version": version,
        "initialized_at": metadata["initialization_time"], "valid_from": first.isoformat().replace("+00:00", "Z"),
        "valid_until": end.isoformat().replace("+00:00", "Z"),
        "bounds": {"min_lat": max(-90, extent["min_lat"] - .125), "max_lat": min(90, extent["max_lat"] + .125),
                   "min_lon": max(-180, extent["min_lon"] - .125), "max_lon": min(180, extent["max_lon"] + .125)},
        "variables": metadata["units"], "files": files,
        "data_sha256": data_sha,
        "ml_status": "pending", "time_convention": metadata["time_convention"]}
    validate_manifest(manifest, "raw")
    try:
        promoted = publish_manifest(store, manifest)
    except CatalogError as error:
        # Another task can complete the same request after our initial existence
        # check, with a different byte serialization but identical science.
        # Reuse only that complete, validated winning manifest; never overwrite.
        try:
            winner = validate_manifest(read_json(store.read(prefix + "manifest.json")), "raw")
        except KeyError:
            raise error
        if winner.get("data_sha256") != data_sha:
            raise
        manifest = winner
        promoted = publish_manifest(store, manifest)
    return {"promoted": promoted, "manifest": manifest}
