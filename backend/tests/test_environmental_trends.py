"""Historical-only trends: units, calendar days, missing hours and cloud publication."""
import json
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from fastapi.testclient import TestClient
from google.api_core.exceptions import Forbidden

from app.config import Settings
from app.dependencies import utc_now
from app.main import create_app
from app.services import environmental_trends as trends, gcs_forecast as gcs


def nasa_source(path,times,now,missing=False,product="analysis"):
    shape=(len(times),2,2)
    hour=np.arange(len(times))[:,None,None]+np.array([[1.,3.],[5.,7.]])[None]
    data=xr.Dataset({name:(("time","lat","lon"),np.broadcast_to(hour*scale,shape).copy(),{"units":unit})
        for name,scale,unit in [("no2",1e-9,"mol mol-1"),("so2",.1e-9,"mol mol-1"),("o3",10e-9,"mol mol-1"),("pm25",2.,"ug m-3")]},
        coords=dict(time=pd.DatetimeIndex(times).tz_localize(None),lat=[33.75,34.],lon=[-84.5,-84.25]),
        attrs=dict(product=product,downloaded_at=now.isoformat(),source_urls=json.dumps(["https://nasa.example/ana/chemistry"])))
    if missing:
        data.no2[80,0,0]=np.nan
    data.to_netcdf(path)
    return data


@pytest.fixture
def published(tmp_path):
    now=datetime(2026,11,19,15,30,tzinfo=timezone.utc)
    times=pd.date_range("2026-11-12T05:30Z",periods=178,freq="h")
    source=tmp_path/"source.nc"
    nasa_source(source,times,now)
    pointer=tmp_path/"current.json"
    trends.publish_analysis(source,pointer,now)
    settings=Settings(environmental_trends_path=str(pointer))
    return settings,now,source,pointer


def test_daily_concentrations_units_and_selected_location(published):
    settings,now,_,_=published
    result=trends.environmental_trends(settings,33.755,-84.495,now)
    assert str(result["period_start"])=="2026-11-12" and str(result["period_end"])=="2026-11-18"
    assert not result["stale"]
    no2,so2,pm25,o3=result["items"]
    assert [x["variable"] for x in result["items"]]==["no2","so2","pm25","o3"]
    assert [x["unit"] for x in result["items"]]==["ppb","ppb","µg/m³","ppb"]
    assert no2["days"][0]["value"]==pytest.approx(12.5)
    assert so2["days"][0]["value"]==pytest.approx(1.25)
    assert pm25["days"][0]["value"]==pytest.approx(25)
    assert o3["days"][0]["value"]==pytest.approx(125)
    assert no2["complete_days"]==7
    assert no2["latest_value"]==pytest.approx(156.5)
    assert str(no2["latest_date"])=="2026-11-18"
    assert no2["change_percent"]==pytest.approx((156.5-12.5)/12.5*100)
    different=trends.environmental_trends(settings,33.755,-84.245,now)
    assert different["items"][0]["latest_value"]!=no2["latest_value"]


def test_incomplete_hours_never_create_daily_means_or_move_the_seven_day_window(published):
    settings,now,source,pointer=published
    times=pd.date_range("2026-11-12T05:30Z",periods=148,freq="h")
    nasa_source(source,times,now,missing=True)
    trends.publish_analysis(source,Path(settings.environmental_trends_path).with_name("partial.json"),now)
    partial=Settings(environmental_trends_path=str(pointer.with_name("partial.json")))
    result=trends.environmental_trends(partial,33.755,-84.495,now)
    assert result["stale"]
    no2=result["items"][0]
    assert no2["days"][-1]["value"] is None and no2["days"][-1]["available_hours"]==4
    assert no2["days"][3]["value"] is None and no2["days"][3]["available_hours"]==23
    assert no2["complete_days"]==5
    assert str(no2["latest_date"])=="2026-11-17"
    assert str(result["period_end"])=="2026-11-18"


def test_calendar_day_handles_dst_25_hours(tmp_path):
    now=datetime(2026,11,3,8,tzinfo=timezone.utc)
    source=tmp_path/"dst.nc";pointer=tmp_path/"current.json"
    nasa_source(source,pd.date_range("2026-10-27T04:30Z","2026-11-03T04:30Z",freq="h"),now)
    trends.publish_analysis(source,pointer,now)
    result=trends.environmental_trends(Settings(environmental_trends_path=str(pointer)),33.755,-84.495,now)
    day=next(x for x in result["items"][0]["days"] if str(x["date"])=="2026-11-01")
    assert day["expected_hours"]==day["available_hours"]==25 and day["value"] is not None


def test_forecasts_wrong_units_and_bad_checksums_are_rejected(published,tmp_path):
    settings,now,source,pointer=published
    data=nasa_source(source,pd.date_range("2026-11-12T05:30Z",periods=168,freq="h"),now,product="forecast")
    with pytest.raises(ValueError,match="never forecasts"):
        trends.write_analysis_artifact(source,tmp_path/"bad.npz",now)
    data.attrs["product"]="analysis";data.no2.attrs["units"]="ppb";data.to_netcdf(source)
    with pytest.raises(ValueError,match="units"):
        trends.write_analysis_artifact(source,tmp_path/"bad.npz",now)
    pub=trends.AnalysisPublication.model_validate_json(pointer.read_text())
    pointer.write_text(pub.model_copy(update={"sha256":"0"*64}).model_dump_json())
    with pytest.raises(ValueError,match="checksum"):
        trends.environmental_trends(settings,33.755,-84.495,now)


def test_public_api_missing_publication_and_location_validation(published,tmp_path):
    settings,now,_,_=published
    app=create_app(settings);app.dependency_overrides[utc_now]=lambda:now
    with TestClient(app) as client:
        r=client.get("/v1/dashboard/environmental-trends?lat=33.755&lon=-84.495")
        assert r.status_code==200 and r.headers["cache-control"]=="no-store"
        assert client.get("/v1/dashboard/environmental-trends?lat=40.7&lon=-74").status_code==404
        assert client.get("/v1/dashboard/environmental-trends?lat=NaN&lon=-84").status_code==422
    with TestClient(create_app(Settings(environmental_trends_path=str(tmp_path/"missing.json")))) as client:
        assert client.get("/v1/dashboard/environmental-trends?lat=33.755&lon=-84.495").status_code==503


def test_cloud_failed_upload_keeps_pointer_and_private_reader_caches(published,monkeypatch,tmp_path):
    settings,now,source,pointer=published
    bucket=Mock();bucket.blob.return_value.upload_from_filename.side_effect=Forbidden("Denied")
    monkeypatch.setattr(gcs,"get_bucket",lambda name:bucket)
    with pytest.raises(Forbidden):
        trends.publish_analysis(source,tmp_path/"upload.json",now,"test")
    bucket.get_blob.assert_not_called()
    assert all(call.args[0]!=trends.CURRENT for call in bucket.blob.call_args_list)
    pub=trends.AnalysisPublication.model_validate_json(pointer.read_text())
    contents=(pointer.parent/pub.artifact).read_bytes()
    remote=pub.model_copy(update={"artifact":f"analysis-trends/{uuid4()}/hourly.npz"})
    archive=Mock();archive.download_to_filename.side_effect=lambda path,**kwargs:path.write_bytes(contents)
    current=Mock();current.download_as_bytes.return_value=remote.model_dump_json().encode()
    bucket=Mock();bucket.blob.side_effect=lambda key:current if key==trends.CURRENT else archive
    monkeypatch.setattr(gcs,"get_bucket",lambda name:bucket)
    monkeypatch.setattr(trends,"gettempdir",lambda:str(tmp_path/"cache"))
    remote_settings=Settings(forecast_gcs_bucket="test")
    a=trends.environmental_trends(remote_settings,33.755,-84.495,now)
    b=trends.environmental_trends(remote_settings,33.755,-84.495,now)
    assert a==b
    archive.download_to_filename.assert_called_once()


def test_old_local_analysis_does_not_replace_current(published):
    settings,now,source,pointer=published
    before=pointer.read_bytes()
    nasa_source(source,pd.date_range("2026-11-12T05:30Z",periods=148,freq="h"),now)
    trends.publish_analysis(source,pointer,now)
    assert pointer.read_bytes()==before


def test_cloud_pointer_retries_generation_races(published,monkeypatch,tmp_path):
    from google.api_core.exceptions import PreconditionFailed
    settings,now,source,pointer=published
    existing=trends.AnalysisPublication.model_validate_json(pointer.read_text())
    current=Mock(generation=17)
    current.download_as_bytes.return_value=existing.model_dump_json().encode()
    current.upload_from_string.side_effect=[PreconditionFailed("raced"),None]
    bucket=Mock()
    bucket.get_blob.side_effect=[None,current]
    bucket.blob.side_effect=lambda key:current if key==trends.CURRENT else Mock()
    monkeypatch.setattr(gcs,"get_bucket",lambda name:bucket)
    selected=trends.publish_analysis(source,tmp_path/"cloud.json",now,"test")
    assert selected.artifact.startswith("analysis-trends/")
    assert [call.kwargs["if_generation_match"] for call in current.upload_from_string.call_args_list]==[0,17]


def test_zero_concentrations_are_real_data_and_future_hours_are_excluded(published,tmp_path):
    settings,now,source,_=published
    times=pd.date_range("2026-11-12T05:30Z",periods=200,freq="h")
    data=nasa_source(source,times,now)
    data.no2[:]=0
    data.to_netcdf(source)
    pointer=tmp_path/"zero.json"
    publication=trends.publish_analysis(source,pointer,now)
    assert publication.observed_through<=now
    result=trends.environmental_trends(Settings(environmental_trends_path=str(pointer)),33.755,-84.495,now)
    no2=result["items"][0]
    assert no2["latest_value"]==0 and no2["complete_days"]==7 and no2["change_percent"] is None
