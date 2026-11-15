"""Finish the Earth Engine geography export: name bands, add lat/lon, check, write.

Input : geography_GA_raw.tif (7 bands from export_geography_GA.js, same grid as the NO2 labels)
Output: geography_GA.tif (9 named float32 bands) and geography_GA_preview.png
Usage : python finish_geography.py RAW_TIF OUT_DIR
"""
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine

GRID = Affine(0.01, 0, -85.60, 0, -0.01, 34.98)
WIDTH, HEIGHT = 408, 420
BANDS = [  # (name, units, description)
    ("elevation_m", "m", "mean elevation, USGS 3DEP 1/3 arc-second"),
    ("lc_developed", "fraction", "NLCD 2021 classes 22-24 (low/medium/high intensity developed)"),
    ("lc_forest", "fraction", "NLCD 2021 classes 41-43"),
    ("lc_farmland", "fraction", "NLCD 2021 classes 81-82 (pasture/hay, cultivated crops)"),
    ("lc_water", "fraction", "NLCD 2021 class 11; cells outside NLCD coverage count as water"),
    ("road_km", "km per cell", "TIGER 2016 primary + secondary roads (MTFCC S1100, S1200), painted at 10 m"),
    ("pop_per_km2", "people per km2", "WorldPop 2020, 100 m"),
    ("latitude", "deg", "cell-center latitude"),
    ("longitude", "deg", "cell-center longitude"),
]


def main(raw_path, out_dir):
    out_dir = Path(out_dir)
    with rasterio.open(raw_path) as src:
        assert (src.width, src.height) == (WIDTH, HEIGHT), (src.width, src.height)
        assert src.transform.almost_equals(GRID, precision=1e-9), src.transform
        assert src.crs.to_epsg() == 4326, src.crs
        assert src.count == 7, src.count
        raw = src.read(masked=True).astype(np.float32).filled(np.nan)
        profile = src.profile

    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH]
    lon = (GRID.c + (cols + 0.5) * GRID.a).astype(np.float32)
    lat = (GRID.f + (rows + 0.5) * GRID.e).astype(np.float32)
    data = np.concatenate([raw, lat[None], lon[None]])

    # Checks: complete coverage, physical ranges, land cover fractions that fit in one cell.
    for i, (name, _, _) in enumerate(BANDS):
        bad = ~np.isfinite(data[i])
        assert not bad.any(), f"{name}: {bad.sum()} missing cells"
    assert -10 < data[0].min() and data[0].max() < 1500, "elevation out of range for Georgia"
    for i in range(1, 5):
        assert data[i].min() >= -1e-4 and data[i].max() <= 1 + 1e-4, BANDS[i][0]
    assert data[1:5].sum(0).max() <= 1 + 1e-3, "land cover fractions sum above 1"
    assert data[5].min() >= 0 and data[6].min() >= 0

    profile.update(count=len(BANDS), dtype="float32", nodata=None, compress="deflate", predictor=3)
    out = out_dir / "geography_GA.tif"
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data)
        dst.update_tags(
            grid="EPSG:4326, 0.01 deg, origin (-85.60, 34.98), 408 x 420; identical to the NO2 labels",
            source_script="export_geography_GA.js + finish_geography.py",
        )
        for i, (name, units, desc) in enumerate(BANDS, 1):
            dst.set_band_description(i, name)
            dst.update_tags(i, units=units, description=desc)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 4, figsize=(16, 8.6), constrained_layout=True)
    extent = [GRID.c, GRID.c + WIDTH * GRID.a, GRID.f + HEIGHT * GRID.e, GRID.f]
    shown = [0, 1, 2, 3, 4, 5, 6]
    for ax, i in zip(axes.flat, shown):
        name, units, _ = BANDS[i]
        img = np.log10(1 + data[i]) if name in ("road_km", "pop_per_km2") else data[i]
        label = f"log10(1 + {units})" if name in ("road_km", "pop_per_km2") else units
        im = ax.imshow(img, extent=extent, cmap="viridis")
        ax.set_title(name)
        ax.plot(-84.388, 33.749, "r+", ms=10)  # downtown Atlanta
        fig.colorbar(im, ax=ax, shrink=0.8, label=label)
    axes.flat[-1].axis("off")
    axes.flat[-1].text(0.02, 0.5, "Red + : downtown Atlanta\nlatitude / longitude bands not shown",
                       transform=axes.flat[-1].transAxes, fontsize=11)
    fig.savefig(out_dir / "geography_GA_preview.png", dpi=110)

    summary = {name: (float(data[i].min()), float(np.median(data[i])), float(data[i].max()))
               for i, (name, _, _) in enumerate(BANDS)}
    for k, (lo, med, hi) in summary.items():
        print(f"{k:14s} min {lo:10.3f}  median {med:10.3f}  max {hi:10.3f}")
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
