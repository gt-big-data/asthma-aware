// Static geography layers for the U-Net, on the exact grid of the NO2 labels
// (EPSG:4326, 0.01 deg, origin -85.60 / 34.98, 408 x 420; same as export_GA_2023.js).
// One multiband GeoTIFF; latitude/longitude are added in the preprocessing notebook.
//   elevation_m       mean elevation (USGS 3DEP 1/3 arc-second, ~10 m)
//   lc_developed      fraction of the cell in NLCD 2021 classes 22-24 (low/medium/high intensity developed)
//   lc_forest         fraction in classes 41-43
//   lc_farmland       fraction in classes 81-82 (pasture/hay, cultivated crops)
//   lc_water          fraction in class 11 (open water); cells NLCD leaves blank (ocean) count as water
//   road_km           km of major road per cell (TIGER 2016 MTFCC S1100 primary + S1200 secondary),
//                     estimated by painting roads at 10 m and counting covered pixels
//   pop_per_km2       WorldPop 2020 (100 m) people per km2
var crs = 'EPSG:4326';
var crsTransform = [0.01, 0, -85.60, 0, -0.01, 34.98];
var box = ee.Geometry.Rectangle([-85.60, 30.78, -81.52, 34.98], null, false);
var utm = 'EPSG:32617'; // only for the painted road raster
var cellKm2 = ee.Image.pixelArea().divide(1e6).reproject({crs: crs, crsTransform: crsTransform});

// Area-weighted mean of a fine image onto the 0.01 deg grid.
// Each input keeps its native projection; reduceResolution averages its pixels into each cell.
// ~108 x 108 ten-meter pixels per cell, so the export uses small shards (see shardSize below).
function toGrid(img) {
  return img.reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 65535})
    .reproject({crs: crs, crsTransform: crsTransform});
}

var elevation = toGrid(ee.Image('USGS/3DEP/10m').select('elevation')).rename('elevation_m');

var lc = ee.Image('USGS/NLCD_RELEASES/2021_REL/NLCD/2021').select('landcover');
function frac(classes, name) {
  return toGrid(lc.remap(classes, ee.List.repeat(1, classes.length), 0).unmask(0)).rename(name);
}
var developed = frac([22, 23, 24], 'lc_developed');
var forest = frac([41, 42, 43], 'lc_forest');
var farmland = frac([81, 82], 'lc_farmland');
var water = toGrid(lc.remap([11], [1], 0).unmask(1)).rename('lc_water');

var roads = ee.FeatureCollection('TIGER/2016/Roads').filterBounds(box)
  .filter(ee.Filter.inList('mtfcc', ['S1100', 'S1200']));
var roadPx = ee.Image(0).byte().paint(roads, 1).setDefaultProjection({crs: utm, scale: 10});
// fraction of 10 m pixels covered x cell area / 0.01 km pixel width = km of road
var roadKm = toGrid(roadPx).multiply(cellKm2).divide(0.01).rename('road_km');

var popCol = ee.ImageCollection('WorldPop/GP/100m/pop').filter(ee.Filter.eq('country', 'USA'))
  .filter(ee.Filter.eq('year', 2020));
var pop = popCol.mosaic().unmask(0).setDefaultProjection(popCol.first().projection());
var popDensity = pop.reduceResolution({reducer: ee.Reducer.sum().unweighted(), maxPixels: 65535})
  .reproject({crs: crs, crsTransform: crsTransform}).divide(cellKm2).rename('pop_per_km2');

var stack = ee.Image.cat([elevation, developed, forest, farmland, water, roadKm, popDensity]).toFloat();
print(stack.bandNames());
Export.image.toDrive({image: stack, description: 'geography_GA_raw', folder: 'EE_GA_geography',
  fileNamePrefix: 'geography_GA_raw', crs: crs, crsTransform: crsTransform, dimensions: '408x420',
  maxPixels: 1e10, shardSize: 32}); // 32-cell shards keep each tile's 10 m input under EE's reprojection limit
print('queued 1 export: run it in the Tasks tab');
