"""Fit a fixed risk reference or build an analysis exposure archive."""
import argparse
from pathlib import Path
from uuid import uuid4

from app.services.risk_artifacts import build_artifact, fit_reference, reference_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("reference", "history"))
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--provisional", action="store_true")
    parser.add_argument("--bucket", help="Upload a history artifact to this bucket")
    args = parser.parse_args()
    if args.action == "reference":
        metadata = fit_reference(args.input, args.output, provisional=args.provisional)
        print(metadata)
        if args.bucket:
            from app.services.gcs_forecast import get_bucket
            key = f"reference/{reference_id(args.output)}.npz"
            get_bucket(args.bucket).blob(key).upload_from_filename(
                args.output, if_generation_match=0, timeout=60,
            )
            print(f"Set RISK_REFERENCE_OBJECT={key}")
    else:
        if args.reference is None:
            parser.error("history requires --reference")
        import xarray as xr
        for path in args.input:
            with xr.open_dataset(path) as data:
                if data.attrs.get("product") != "analysis":
                    parser.error("Historical training exposure must come from analysis files")
        build_artifact(
            args.input, args.reference, args.output,
            issued_at="1970-01-01T00:00:00Z", source_run="historical-analysis", product="analysis",
        )
        if args.bucket:
            from app.services.gcs_forecast import get_bucket
            get_bucket(args.bucket).blob(f"history/{uuid4()}/risk.npz").upload_from_filename(
                args.output, if_generation_match=0, timeout=60,
            )
        print(f"Historical exposure archive written to {args.output}")


if __name__ == "__main__":
    main()
