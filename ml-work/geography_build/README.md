# Geography layers

`ml-work/geography_GA.tif` holds 9 static float32 bands on the exact label grid
(EPSG:4326, 0.01 deg, origin -85.60 / 34.98, 408 x 420): elevation, NLCD 2021 land-cover
fractions (developed, forest, farmland, water), TIGER primary/secondary road length,
WorldPop 2020 density, and cell-center latitude/longitude. Band names and units are in
the file's band descriptions; `geography_config.json` points the training code at it.

To rebuild:
1. Run `export_geography_GA.js` in the Earth Engine Code Editor; start the Drive export
   from the Tasks panel. It writes `geography_GA_raw.tif` (7 bands).
2. `python finish_geography.py geography_GA_raw.tif OUT_DIR` names the bands, adds
   latitude/longitude, checks the grid, and writes `geography_GA.tif` plus a preview PNG.
