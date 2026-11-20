"""Publish the freshest seven-day NASA analysis history for dashboard trends."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from app.config import Settings
from app.services.environmental_trends import publish_analysis


def download_latest(now, output=Path("data/geos_cf")):
    from ingestion.geos_cf import fetch_analysis as analysis, fetch_forecast as nasa
    from scripts.fetch_risk_analysis import download
    # All four pollutants use this chemistry collection. The archive time axis
    # establishes the available analysis horizon; never request future hours.
    url=analysis.analysis_url(nasa.AQC_COLLECTION)
    _,axis,_=nasa.remote_call(nasa._read_inventory,url,what="Finding latest NASA analysis",retries=2)
    end=min(pd.Timestamp(now).tz_convert("UTC").tz_localize(None).floor("h"),pd.Timestamp(axis[-1])+pd.Timedelta(minutes=30))
    today=now.astimezone(ZoneInfo("America/New_York")).date()
    start=pd.Timestamp(today-timedelta(days=7),tz="America/New_York").tz_convert("UTC").tz_localize(None)
    if end<=start:
        raise ValueError("NASA has no recent analysis for the last seven days")
    return download(start,end,output)


def publish_latest(*,source=None,settings=None,now=None):
    settings=settings or Settings.from_env()
    requested_at=now or datetime.now(timezone.utc)
    source=source or download_latest(requested_at)
    published_at=now or datetime.now(timezone.utc)
    return publish_analysis(source,Path(settings.environmental_trends_path),published_at,settings.forecast_gcs_bucket)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,help="Reuse a recent NASA analysis NetCDF")
    args=parser.parse_args()
    publication=publish_latest(source=args.input)
    print(f"Published analysis through {publication.observed_through.isoformat()}")


if __name__=="__main__":
    main()
