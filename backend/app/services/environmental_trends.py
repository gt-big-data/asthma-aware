"""Location-specific historical concentrations, never forecasts or risk exposures."""
import json
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from tempfile import gettempdir
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import xarray as xr
from google.api_core.exceptions import PreconditionFailed
from pydantic import AwareDatetime, BaseModel, Field

from app.services.location_catalog import location_coverage
from app.services.risk_artifacts import read_surface, reference_id

CURRENT = "analysis-trends/current.json"
ZONE = ZoneInfo("America/New_York")
VARIABLES = (("no2", "NO₂", "ppb"), ("so2", "SO₂", "ppb"), ("pm25", "PM2.5", "µg/m³"), ("o3", "O₃", "ppb"))


class AnalysisPublication(BaseModel):
    artifact: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_through: AwareDatetime
    downloaded_at: AwareDatetime


def write_analysis_artifact(source: Path, output: Path, now: datetime):
    """Small native-grid archive: four hourly concentrations in physical units."""
    with xr.open_dataset(source) as raw:
        if raw.attrs.get("product") != "analysis":
            raise ValueError("Environmental history requires NASA analysis, never forecasts")
        downloaded = pd.Timestamp(raw.attrs["downloaded_at"])
        if downloaded.tzinfo is None or downloaded > now:
            raise ValueError("Invalid analysis download timestamp")
        source_urls = raw.attrs.get("source_urls", [])
        if isinstance(source_urls, str):
            source_urls = json.loads(source_urls)
    data = read_surface(source)
    times = pd.DatetimeIndex(data.time.values).tz_localize("UTC")
    selected = (times + timedelta(minutes=30) <= now) & (times >= now - timedelta(days=10))
    data = data.isel(time=selected)
    if not len(data.time):
        raise ValueError("No recent completed analysis hours")
    timeline = pd.date_range(data.time.values[0], data.time.values[-1], freq="h")
    data = data.reindex(time=timeline)
    values = np.stack([data[name].values for name, _, _ in VARIABLES], axis=1).astype(np.float32)
    valid = np.flatnonzero(np.isfinite(values).any(axis=(1,2,3)))
    if not len(valid):
        raise ValueError("Analysis contains no recent finite concentrations")
    end = int(valid[-1]) + 1
    times = pd.DatetimeIndex(timeline[:end]).tz_localize("UTC")
    metadata = dict(product="analysis", source_sha256=reference_id(source), source_urls=source_urls,
        downloaded_at=downloaded.isoformat(), variables=[name for name, _, _ in VARIABLES])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        np.savez_compressed(handle, times=times.asi8, values=values[:end],
            latitudes=data.lat.values, longitudes=data.lon.values, metadata=json.dumps(metadata))
    return AnalysisPublication(artifact=output.name, sha256=reference_id(output),
        observed_through=(times[-1] + timedelta(minutes=30)).to_pydatetime(), downloaded_at=downloaded.to_pydatetime())


@lru_cache(maxsize=2)
def load_analysis(path: Path, modified: int, size: int, digest: str):
    if reference_id(path) != digest:
        raise ValueError("Analysis checksum differs from its publication")
    with np.load(path, allow_pickle=False) as data:
        times = pd.to_datetime(data["times"], utc=True)
        values, latitudes, longitudes = data["values"], data["latitudes"], data["longitudes"]
        metadata = json.loads(str(data["metadata"]))
    if metadata.get("product") != "analysis" or metadata.get("variables") != [name for name,_,_ in VARIABLES]:
        raise ValueError("Invalid analysis artifact")
    if (values.shape != (len(times),4,len(latitudes),len(longitudes)) or not 1 <= len(times) <= 241
            or not times.is_unique or not np.all(np.diff(times.asi8) == 3600000000000)
            or not np.all(times.minute == 30) or not np.all(times.second == 0) or np.isinf(values).any() or np.any(values<0)
            or any(len(axis)<2 or not np.allclose(np.diff(axis),.25) for axis in (latitudes,longitudes))):
        raise ValueError("Invalid analysis grid or time axis")
    return times,values,latitudes,longitudes,metadata


def publish_analysis(source: Path, output: Path, now: datetime, bucket_name=""):
    """Advance a separate analysis pointer without changing risk/forecast runs."""
    from app.services.gcs_forecast import get_bucket
    output.parent.mkdir(parents=True, exist_ok=True)
    name = f"analysis-trends-{uuid4()}.npz"
    archive = output.parent / name
    pointer = write_analysis_artifact(source, archive, now)
    try:
        if bucket_name:
            key = f"analysis-trends/{uuid4()}/hourly.npz"
            bucket = get_bucket(bucket_name)
            bucket.blob(key).upload_from_filename(archive, content_type="application/octet-stream", if_generation_match=0, timeout=60)
            pointer = pointer.model_copy(update={"artifact":key})
            for _ in range(5):
                try:
                    current = bucket.get_blob(CURRENT, timeout=10)
                    generation = int(current.generation) if current else 0
                    if current:
                        old = AnalysisPublication.model_validate_json(current.download_as_bytes(if_generation_match=generation, timeout=10))
                        if (old.observed_through,old.downloaded_at) > (pointer.observed_through,pointer.downloaded_at):
                            return old
                    bucket.blob(CURRENT).upload_from_string(pointer.model_dump_json(), content_type="application/json", if_generation_match=generation, timeout=10)
                    return pointer
                except PreconditionFailed:
                    continue
            raise RuntimeError("Analysis publication changed repeatedly")
        if output.exists():
            old = AnalysisPublication.model_validate_json(output.read_text(encoding="utf-8"))
            if (old.observed_through,old.downloaded_at) > (pointer.observed_through,pointer.downloaded_at):
                archive.unlink(missing_ok=True)
                return old
        pending = output.with_name(f".{output.name}.{uuid4()}.tmp")
        try:
            pending.write_text(pointer.model_dump_json(),encoding="utf-8")
            pending.replace(output)
        finally:
            pending.unlink(missing_ok=True)
        return pointer
    finally:
        if bucket_name:
            archive.unlink(missing_ok=True)


def resolve_analysis(settings):
    if settings.forecast_gcs_bucket:
        from app.services.gcs_forecast import get_bucket
        bucket = get_bucket(settings.forecast_gcs_bucket)
        pointer = AnalysisPublication.model_validate_json(bucket.blob(CURRENT).download_as_bytes(timeout=10))
        parts=pointer.artifact.split("/")
        if len(parts)!=3 or parts[0]!="analysis-trends" or parts[2]!="hourly.npz":
            raise ValueError("Invalid analysis object key")
        UUID(parts[1])
        directory=Path(gettempdir()) / "asthmaaware-analysis-trends" / settings.forecast_gcs_bucket
        directory.mkdir(parents=True,exist_ok=True)
        path=directory / f"{pointer.sha256}.npz"
        if not path.exists():
            pending=directory / f".{uuid4()}.tmp"
            try:
                bucket.blob(pointer.artifact).download_to_filename(pending,timeout=60)
                if reference_id(pending)!=pointer.sha256:
                    raise ValueError("Analysis checksum mismatch")
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
            for old in sorted(directory.glob("*.npz"),key=lambda p:p.stat().st_mtime_ns,reverse=True)[2:]:
                try: old.unlink(missing_ok=True)
                except PermissionError: pass
    else:
        output=Path(settings.environmental_trends_path)
        pointer=AnalysisPublication.model_validate_json(output.read_text(encoding="utf-8"))
        name=Path(pointer.artifact)
        if name.name!=str(name) or not name.name.endswith(".npz"):
            raise ValueError("Invalid analysis filename")
        path=output.parent / name
    info=path.stat()
    archive=load_analysis(path,info.st_mtime_ns,info.st_size,pointer.sha256)
    if (archive[0][-1].to_pydatetime()+timedelta(minutes=30)!=pointer.observed_through
            or pd.Timestamp(archive[4]["downloaded_at"]).to_pydatetime()!=pointer.downloaded_at):
        raise ValueError("Analysis pointer and artifact differ")
    return pointer,archive


def environmental_trends(settings, latitude, longitude, now):
    if not location_coverage(latitude,longitude)["in_georgia"]:
        raise LookupError("Choose a location in Georgia")
    pointer,(times,values,latitudes,longitudes,metadata)=resolve_analysis(settings)
    row=int(np.floor((latitude-latitudes[0]+.125)/.25))
    col=int(np.floor((longitude-longitudes[0]+.125)/.25))
    if not 0<=row<len(latitudes) or not 0<=col<len(longitudes):
        raise LookupError("Location is outside the analysis grid")
    today=now.astimezone(ZONE).date()
    dates=[today-timedelta(days=offset) for offset in range(7,0,-1)]
    items=[]
    for channel,(name,label,unit) in enumerate(VARIABLES):
        days=[]
        point=values[:,channel,row,col]
        for day in dates:
            start=datetime.combine(day,time.min,ZONE).astimezone(timezone.utc)
            end=datetime.combine(day+timedelta(days=1),time.min,ZONE).astimezone(timezone.utc)
            expected=int((end-start).total_seconds()/3600)
            indices=(times>=start)&(times<end)&(times+timedelta(minutes=30)<=now)
            available=int(np.isfinite(point[indices]).sum())
            mean=float(point[indices].mean(dtype=np.float64)) if available==expected else None
            days.append(dict(date=day,value=mean,available_hours=available,expected_hours=expected))
        complete=[day for day in days if day["value"] is not None]
        latest=complete[-1] if complete else None
        first=days[0]["value"]
        change=(latest["value"]-first)/first*100 if first is not None and first>1e-6 and latest is not None and latest["date"]>dates[0] else None
        items.append(dict(variable=name,label=label,unit=unit,days=days,
            latest_value=latest["value"] if latest else None,latest_date=latest["date"] if latest else None,
            complete_days=len(complete),change_percent=change))
    return dict(source="NASA GEOS-CF analysis",timezone="America/New_York",aggregation="daily_mean",
        latitude=latitude,longitude=longitude,source_grid_degrees=.25,
        period_start=dates[0],period_end=dates[-1],analysis_through=pointer.observed_through,
        downloaded_at=pointer.downloaded_at,analysis_lag_hours=round(max(0,(now-pointer.observed_through).total_seconds()/3600),1),
        stale=pointer.observed_through<datetime.combine(today,time.min,ZONE).astimezone(timezone.utc),items=items)
