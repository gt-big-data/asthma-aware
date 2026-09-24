"""Build the model input sequence from the database.

    python scripts/build_latest_sequence_from_db.py

This is the database-backed counterpart to
``scripts/build_latest_sequence.py``. Both produce
``app/data/latest_observed_sequence.npy`` with shape ``(4, 3, 56, 96)``,
so the rest of the backend does not change. The original is left in
place and still works from ``raw_rasters/``.

Three things this does that the file-based version cannot:

**Stable normalisation.** It uses the frozen constants from
``scaler_params`` rather than refitting a MinMaxScaler on every run, so
the values fed to the model stay on the same scale the model was trained
on. This is the main reason to switch.

**Honest handling of gaps.** It selects the most recent dates that
actually have complete frames for every feature, and can require them to
be consecutive calendar days. The file-based version just takes the last
four slices of whatever is on disk and cannot tell a gap from a
continuous run.

**Provenance.** It reports exactly which dates went into the sequence,
which is otherwise unrecoverable from the .npy alone.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.constants import FEATURE_ORDER  # noqa: E402
from db.repositories import grid as grid_repo  # noqa: E402
from db.repositories import reference  # noqa: E402
from db.repositories import scalers as scaler_repo  # noqa: E402
from db.session import session_scope  # noqa: E402

OUTPUT_PATH = BACKEND_DIR / "app" / "data" / "latest_observed_sequence.npy"

# The ConvLSTM's input window. app/ml/base_convlstm.pt expects exactly
# this many time steps; see backend/README.md.
SEQUENCE_LENGTH = 4


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--region", default="atlanta")
    parser.add_argument(
        "--steps",
        type=int,
        default=SEQUENCE_LENGTH,
        help=f"Time steps in the sequence (default: {SEQUENCE_LENGTH}, what the model expects)",
    )
    parser.add_argument(
        "--variables",
        nargs="*",
        default=None,
        help=f"Channel order (default: FEATURE_ORDER, {FEATURE_ORDER})",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.8,
        help=(
            "Fraction of the grid that must be non-NULL for a frame to count "
            "as complete. Sentinel-5P is often partly cloud-masked, so 1.0 "
            "can legitimately match nothing. Default: 0.8"
        ),
    )
    parser.add_argument(
        "--require-consecutive",
        action="store_true",
        help=(
            "Require the selected dates to be adjacent calendar days. The "
            "model was trained on a daily cadence; four frames spanning three "
            "weeks is not something the code can otherwise detect."
        ),
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Skip scaling and write values in their canonical units.",
    )
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--dry-run", action="store_true", help="Report, but write nothing.")
    args = parser.parse_args(argv)

    variable_slugs = args.variables or list(FEATURE_ORDER)

    with session_scope() as session:
        region = reference.get_region(session, args.region)
        variables = reference.get_variables(session, variable_slugs)

        dates = grid_repo.latest_complete_dates(
            session,
            region,
            variables,
            args.steps,
            require_consecutive=args.require_consecutive,
            min_coverage=args.min_coverage,
        )
        print(f"Region:    {region.slug} ({region.grid_rows}x{region.grid_cols})")
        print(f"Channels:  {variable_slugs}")
        print(f"Dates:     {', '.join(d.isoformat() for d in dates)}")

        sequence = grid_repo.load_sequence(session, region, variables, dates)

        if args.raw:
            print("Scaling:   none (--raw)")
        else:
            active = scaler_repo.get_active_many(session, region, variables)
            print(
                "Scaling:   "
                + ", ".join(
                    f"{s.variable_slug}={s.method}/{s.version}" for s in active
                )
            )
            sequence = scaler_repo.transform_sequence(sequence, active)

    nan_count = int(np.count_nonzero(np.isnan(sequence)))
    if nan_count:
        # The model has no NaN handling, so this has to be dealt with
        # rather than passed through. Filling with 0 post-scaling is what
        # the existing pipeline effectively does via np.nan_to_num.
        print(
            f"\nWARNING: {nan_count} of {sequence.size} values are NaN "
            f"({100.0 * nan_count / sequence.size:.1f}%) -- masked or missing "
            f"pixels. Filling with 0, matching the existing pipeline's "
            f"np.nan_to_num. Raise --min-coverage if this is too high."
        )
        sequence = np.nan_to_num(sequence, nan=0.0)

    sequence = sequence.astype(np.float32)
    print(f"\nShape:     {sequence.shape}")
    print(
        f"Range:     {float(sequence.min()):.6g} .. {float(sequence.max()):.6g}"
    )

    expected = (args.steps, len(variable_slugs), region.grid_rows, region.grid_cols)
    if sequence.shape != expected:
        raise SystemExit(f"Expected shape {expected}, got {sequence.shape}")

    if args.dry_run:
        print("\n[DRY RUN] nothing written.")
        return

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.save(output, sequence)
    print(f"\nWrote {output}")


if __name__ == "__main__":
    main()
