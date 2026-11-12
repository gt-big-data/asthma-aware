"""Prepare independent source indexes; no model training is performed."""

import argparse
import json
from pathlib import Path
from .data_pipeline import (
    load_config,
    build_catalog,
    write_bundle,
    SourceDataset,
    fit_normalization,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fit-normalization",
        action="store_true",
        help="Fit training-only statistics for ready sources",
    )
    args = parser.parse_args()
    bundle = build_catalog(load_config(args.config))
    write_bundle(bundle, args.output)
    if args.fit_normalization:
        for source, report in bundle["reports"].items():
            if report["ready"]:
                ds = SourceDataset(bundle, source, "train")
                try:
                    norm = fit_normalization(ds)
                finally:
                    ds.close()
                (args.output / f"{source}_normalization.json").write_text(
                    json.dumps(norm, indent=2) + "\n"
                )
    for source, report in bundle["reports"].items():
        print(
            f"{source}: {'ready' if report['ready'] else 'blocked'}; {report['temporal_matches']} temporal matches"
        )
        for reason in report["blockers"]:
            print("  " + reason)


if __name__ == "__main__":
    main()
