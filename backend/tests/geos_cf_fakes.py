"""A small stand-in for NASA's GEOS-CF OPeNDAP collections, shared by the GEOS-CF tests."""

import numpy as np
import pandas as pd
import xarray as xr

from ingestion.geos_cf import fetch_forecast as ff

INIT = pd.Timestamp("2026-10-15T09:00")


def fake_remote(collection, hours=120):
    """A small stand-in for a GEOS-CF OPeNDAP collection, shaped like the real one."""
    times = pd.date_range(INIT + ff.TAVG_OFFSET, periods=hours, freq="h")
    lat = np.arange(25.0, 40.01, 0.25)
    lon = np.arange(-90.0, -75.01, 0.25)
    shape = (hours, 1, lat.size, lon.size)
    data_vars = {}
    for spec in ff.VARIABLES:
        if spec.collection == collection:
            data_vars[spec.source_name] = (
                ("time", "lev", "lat", "lon"),
                np.random.default_rng(0).random(shape, dtype=np.float32),
                {"long_name": f"{spec.source_name} thing ({spec.units}) "},
            )
    return xr.Dataset(data_vars, coords={"time": times, "lev": [72.0], "lat": lat, "lon": lon})
