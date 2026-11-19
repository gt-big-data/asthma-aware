"""PM2.5 products built on the GEOS-CF analysis.

* ``daily_grid``  -- hourly analysis NetCDF -> daily (days, height, width) GeoTIFF, with a
                     gap-filling method comparison when values are missing.
* ``monitors``    -- AirNow's public daily archive -> regulatory monitor PM2.5 (no API key).
* ``features``    -- the 1 km "clues": land cover, roads, elevation, weather, GEOS-CF.
* ``downscale``   -- learn how monitors differ from GEOS-CF and predict a 1 km daily grid.
"""
