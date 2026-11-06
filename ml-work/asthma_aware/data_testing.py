"""Print TIFF metadata without loading raster pixels into memory.

Run from the repository root:
    python3 ml-work/asthma_aware/data_testing.py

Optionally pass one or more TIFF paths to inspect specific files.
"""

import argparse
from pathlib import Path
from pprint import pprint
from collections import Counter

import rasterio


def inspect_tiff(path: Path) -> None:
    """Report spatial shape, full array shape, and GeoTIFF metadata."""
    with rasterio.open(path) as src:
        print(f"\n{'=' * 80}\nFile: {path.name}\nPath: {path.resolve()}")
        pprint(
            {
                "shape (height, width)": src.shape,
                "array shape (bands, height, width)": (
                    src.count,
                    src.height,
                    src.width,
                ),
                "profile": dict(src.profile),
                "CRS": str(src.crs) if src.crs else None,
                "bounds": tuple(src.bounds),
                "resolution (x, y)": src.res,
                "dataset tags": src.tags(),
                "additional tag namespaces": {
                    namespace: src.tags(ns=namespace)
                    for namespace in src.tag_namespaces()
                },
            },
            sort_dicts=False,
        )
        for i in range(0, min(20, len(src.indexes))):
            band = src.indexes[i]
            print(f"\nBand {band}:")
            pprint(
                {
                    "dtype": src.dtypes[band - 1],
                    "description": src.descriptions[band - 1],
                    "units": src.units[band - 1],
                    "nodata": dict(Counter(src.nodatavals[band - 1])),
                    "scale": src.scales[band - 1],
                    "offset": src.offsets[band - 1],
                    "color interpretation": src.colorinterp[band - 1].name,
                    "mask flags": [
                        flag.name for flag in src.mask_flag_enums[band - 1]
                    ],
                    "block shape (height, width)": src.block_shapes[band - 1],
                    "overview downsampling factors": src.overviews(band),
                    "tags": src.tags(band),
                    "additional tag namespaces": {
                        namespace: src.tags(band, ns=namespace)
                        for namespace in src.tag_namespaces(band)
                    },
                },
                sort_dicts=False,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="TIFF files to inspect")
    args = parser.parse_args()
    data_dir = Path(__file__).resolve().parents[1]
    paths = args.paths or sorted(
        path
        for path in data_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}
    )
    if not paths:
        parser.error(f"No TIFF files found in {data_dir}")
    for path in paths:
        inspect_tiff(path)


if __name__ == "__main__":
    main()
