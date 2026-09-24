"""Load the existing raw_rasters/ history into the database.

The repo ships three raster stacks from the ML team covering the model's
training period:

    NO2_reprojected_stack.tif              181 daily bands, 56x96
    SO2_stack_reprojected_ndvi_dims2.tif   181 daily bands, 56x96
    ndvi_stack.tif                          12 composite bands, 56x96

This script writes them into ``grid_observations`` so the team starts
with real history instead of an empty database -- which matters
immediately, because the scaler fit (scripts/fit_scalers.py) needs
history to be meaningful.

    python scripts/backfill_raw_rasters.py --start-date 2025-01-01

Two things worth knowing before running it:

**The start date is not in the files.** The stacks carry no per-band
timestamps, so band-to-date mapping has to be supplied. Ask whoever
produced the stacks which calendar date band 1 corresponds to. Getting
this wrong does not corrupt the arrays, but it misaligns this history
against anything the live collector ingests later.

**The files are NOT reprojected here, deliberately.** Their embedded CRS
is mis-tagged -- MODIS spherical sinusoidal data carrying a WGS84
ellipsoid WKT -- so honouring it would shift everything ~17km north and
discard ~60% of the grid. The arrays are already exactly the 56x96
Atlanta grid (verified: under the correct MODIS sphere the footprint
matches ATLANTA_BOUNDS to within 0.002 degrees), so they are read
straight through. See ingestion/grid_resample.py for the detail.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from db.repositories import grid as grid_repo  # noqa: E402
from db.repositories import ingestion as ingestion_repo  # noqa: E402
from db.repositories import reference  # noqa: E402
from db.session import session_scope  # noqa: E402
from ingestion import grid_resample  # noqa: E402

RASTER_DIR = BACKEND_DIR / "raw_rasters"

# MOD13Q1 is a 16-day composite. build_latest_sequence.py expands it to
# daily with np.repeat(ndvi, 16, axis=0), and this mirrors that exactly so
# the backfilled history matches what the model was trained against.
NDVI_COMPOSITE_DAYS = 16

# MODIS NDVI ships as int16 scaled by 1e-4. ingestion/satellite/client.py
# applies this factor on the way out of Earth Engine, so the backfill must
# apply it too -- otherwise historical NDVI is stored 10,000x larger than
# anything the live collector writes, and the scaler fit silently
# straddles both.
STACKS = [
    {
        "variable": "no2",
        "source": "sentinel-5p-tropomi-l3-offl",
        "filename": "NO2_reprojected_stack.tif",
        "scale_factor": 1.0,
        "cadence_days": 1,
    },
    {
        "variable": "so2",
        "source": "sentinel-5p-tropomi-l3-offl",
        "filename": "SO2_stack_reprojected_ndvi_dims2.tif",
        "scale_factor": 1.0,
        "cadence_days": 1,
    },
    {
        "variable": "ndvi",
        "source": "modis-mod13q1",
        "filename": "ndvi_stack.tif",
        "scale_factor": 1e-4,
        "cadence_days": NDVI_COMPOSITE_DAYS,
    },
]


def _describe(values: np.ndarray) -> str:
    valid = np.count_nonzero(~np.isnan(values))
    if valid == 0:
        return "all NaN"
    return (
        f"{valid}/{values.size} valid, "
        f"range {np.nanmin(values):.4g}..{np.nanmax(values):.4g}"
    )


def backfill_stack(
    session,
    run,
    region,
    spec: Dict,
    start_date: dt.date,
    *,
    expand_composites: bool,
    limit: Optional[int],
    dry_run: bool,
) -> int:
    path = RASTER_DIR / spec["filename"]
    if not path.exists():
        print(f"  SKIP {spec['variable']}: {path} not found")
        ingestion_repo.record_issue(
            session,
            run,
            f"{path} not found",
            variable=reference.get_variable(session, spec["variable"]),
            kind="skipped",
        )
        return 0

    variable = reference.get_variable(session, spec["variable"])
    stack = grid_resample.read_band_stack(path)

    bands, height, width = stack.shape
    if (height, width) != (region.grid_rows, region.grid_cols):
        raise SystemExit(
            f"{path.name} is {height}x{width} but region {region.slug!r} "
            f"declares a {region.grid_rows}x{region.grid_cols} grid. "
            f"These must match -- the model depends on it."
        )

    if spec["scale_factor"] != 1.0:
        stack = stack * spec["scale_factor"]

    cadence = spec["cadence_days"]
    written = 0
    frames_written = 0

    for band_index in range(bands):
        frame = stack[band_index]
        source_date = start_date + dt.timedelta(days=band_index * cadence)

        if cadence > 1 and expand_composites:
            # Write the composite to every day it covers, recording the
            # real acquisition date in source_date so the duplication is
            # never mistaken for genuine daily observation.
            target_dates = [
                source_date + dt.timedelta(days=offset) for offset in range(cadence)
            ]
        else:
            target_dates = [source_date]

        for target_date in target_dates:
            if limit is not None and frames_written >= limit:
                return written
            if not dry_run:
                written += grid_repo.upsert_frame(
                    session,
                    region,
                    variable,
                    target_date,
                    frame,
                    source_date=source_date,
                    run_id=run.id,
                )
            frames_written += 1

    last = start_date + dt.timedelta(days=(bands - 1) * cadence)
    print(
        f"  {spec['variable']:5s} {bands:3d} band(s) -> {frames_written:3d} frame(s), "
        f"{start_date} .. {last}"
    )
    print(f"        band 1: {_describe(stack[0])}")
    return written


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Calendar date of band 1. Ask the ML team; it is not stored in the files.",
    )
    parser.add_argument("--region", default="atlanta", help="Region slug (default: atlanta)")
    parser.add_argument(
        "--no-expand-composites",
        action="store_true",
        help=(
            "Write each NDVI composite only to its acquisition date, rather "
            "than to all 16 days it covers. The model expects daily NDVI, so "
            "the default (expand) matches build_latest_sequence.py."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only write this many frames per variable. Useful for a quick smoke test.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and report, but write nothing.",
    )
    args = parser.parse_args(argv)

    start_date = dt.date.fromisoformat(args.start_date)

    with session_scope() as session:
        region = reference.get_region(session, args.region)
        with ingestion_repo.run_scope(
            session,
            "backfill_raw_rasters",
            region=region,
            start=start_date,
            params=vars(args),
        ) as run:
            print(
                f"Backfilling raw_rasters/ into region {region.slug!r} "
                f"({region.grid_rows}x{region.grid_cols}), band 1 = {start_date}"
                + ("  [DRY RUN]" if args.dry_run else "")
            )
            total = 0
            for spec in STACKS:
                total += backfill_stack(
                    session,
                    run,
                    region,
                    spec,
                    start_date,
                    expand_composites=not args.no_expand_composites,
                    limit=args.limit,
                    dry_run=args.dry_run,
                )
            run.rows_written = total
            print(f"\n{total} observation rows written.")

    if not args.dry_run:
        print("\nNext: fit the scalers over this history:")
        print(f"  python scripts/fit_scalers.py --region {args.region} --version v1 --activate")


if __name__ == "__main__":
    main()
