"""Fit and freeze the normalisation constants used for model input.

    python scripts/fit_scalers.py --region atlanta --version v1 --activate

Why this exists
---------------
``scripts/build_latest_sequence.py`` fits a fresh ``MinMaxScaler`` over
the whole raster stack on every run. That means the normalisation is
re-derived from whatever data happens to be on disk, so every new
observation that sets a new min or max shifts *all* historical values.
The model was trained under one scaling and is served under another, and
nothing anywhere raises an error -- the forecasts just quietly degrade.

Fitting once and recording the constants fixes that. Values are stored
raw in the database, and scaling is applied at read time from a pinned,
versioned set of constants.

When you retrain the model, fit a new ``--version`` and activate it. Old
versions are kept so past forecasts remain interpretable under the
constants that actually produced them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import List, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.constants import FEATURE_ORDER  # noqa: E402
from db.repositories import reference  # noqa: E402
from db.repositories import scalers as scaler_repo  # noqa: E402
from db.session import session_scope  # noqa: E402


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--region", default="atlanta")
    parser.add_argument(
        "--version",
        default="v1",
        help="Label for this set of constants. Bump it when retraining.",
    )
    parser.add_argument(
        "--variables",
        nargs="*",
        default=None,
        help=f"Variables to fit (default: the model's feature order, {FEATURE_ORDER})",
    )
    parser.add_argument("--start-date", default=None, help="Restrict the fit window (inclusive)")
    parser.add_argument("--end-date", default=None, help="Restrict the fit window (inclusive)")
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Make this version the one the sequence builder uses.",
    )
    parser.add_argument("--notes", default=None)
    args = parser.parse_args(argv)

    variable_slugs = args.variables or list(FEATURE_ORDER)
    start = dt.date.fromisoformat(args.start_date) if args.start_date else None
    end = dt.date.fromisoformat(args.end_date) if args.end_date else None

    with session_scope() as session:
        region = reference.get_region(session, args.region)
        print(
            f"Fitting {args.version!r} minmax scalers for region {region.slug!r}"
            + (f" over {start or 'start'}..{end or 'now'}" if (start or end) else "")
        )
        for slug in variable_slugs:
            variable = reference.get_variable(session, slug)
            params = scaler_repo.fit_minmax(
                session,
                region,
                variable,
                version=args.version,
                start=start,
                end=end,
                activate=args.activate,
                notes=args.notes,
            )
            span = (params.fit_max or 0) - (params.fit_min or 0)
            flag = "  <-- ACTIVE" if args.activate else ""
            print(
                f"  {slug:6s} min={params.fit_min:.6g}  max={params.fit_max:.6g}  "
                f"span={span:.6g}  n={params.sample_count:,}{flag}"
            )
            if span == 0:
                print(
                    f"         WARNING: {slug} is constant over this window. "
                    f"It carries no signal and will scale to all zeros."
                )

    if not args.activate:
        print(
            f"\nFitted but not activated. Re-run with --activate, or activate later, "
            f"before the sequence builder can use these."
        )


if __name__ == "__main__":
    main()
