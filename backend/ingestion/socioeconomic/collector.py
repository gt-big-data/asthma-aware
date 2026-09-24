"""Census ACS socioeconomic ingestion.

Fetches ACS 5-year estimates for the configured ZCTAs and writes them to
Postgres, so the API can serve them from the database instead of calling
the Census API on every cold cache miss.

    python -m ingestion.socioeconomic.collector
    python -m ingestion.socioeconomic.collector --year 2023 --region atlanta

ACS 5-year estimates are published annually, so this is a once-a-year
job, not a scheduled one. Re-running is safe: records are keyed on
(ZCTA, dataset_year) and updated in place.

The fetch and transform logic is imported from
``app.services.socioeconomic_service`` rather than reimplemented. That
module already handles the parts that are easy to get wrong -- the ACS
sentinel values (-666666666 and friends) that mean "not available" and
must become NULL rather than a number, and the derived-rate arithmetic
that has to tolerate a missing numerator or denominator. Duplicating it
here would mean two copies drifting apart.

Run from the backend/ directory.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import socioeconomic_service as acs  # noqa: E402
from db.repositories import ingestion as ingestion_repo  # noqa: E402
from db.repositories import reference  # noqa: E402
from db.repositories import socioeconomic as socio_repo  # noqa: E402
from db.session import session_scope  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SOURCE_SLUG = "us-census-acs5"


def _raw_row_for(zcta: str, raw_rows: List[List[str]]) -> Optional[Dict[str, str]]:
    """Pull one ZCTA's untransformed ACS variables out of the raw response.

    Stored alongside the derived indicators because the derivation is
    lossy: a NULL poverty_rate cannot tell you whether the numerator or
    the denominator was missing, and re-deriving beats re-fetching a
    vintage that may no longer be served.
    """
    headers = raw_rows[0]
    try:
        zcta_col = headers.index("zip code tabulation area")
    except ValueError:
        return None
    for row in raw_rows[1:]:
        if row[zcta_col] == zcta:
            return dict(zip(headers, row))
    return None


def collect(
    session,
    run,
    region,
    *,
    dataset_year: int,
    city: str,
) -> int:
    raw_rows = acs._fetch_census_rows()
    indicators = acs._transform_rows(raw_rows, city)
    logger.info("Census returned %d ZCTA rows for %s", len(indicators), city)

    written = 0
    for record in indicators:
        zcta_code = record["zipcode"]
        zcta = socio_repo.upsert_zcta(
            session,
            zcta_code,
            region=region,
            area_sq_miles=record.get("area_sq_miles"),
        )
        socio_repo.upsert_record(
            session,
            zcta=zcta,
            dataset_year=dataset_year,
            source=f"us_census_acs5_{dataset_year}",
            indicators=record,
            raw=_raw_row_for(zcta_code, raw_rows),
            run_id=run.id,
        )
        written += 1

    # Every configured ZCTA that the Census did not return is worth
    # recording: it usually means the ZCTA list and the ACS vintage have
    # drifted apart, which is invisible otherwise.
    returned = {r["zipcode"] for r in indicators}
    for zcta_code in acs.SUPPORTED_CITY_ZCTAS.get(city, ()):
        if zcta_code not in returned:
            logger.warning("ZCTA %s not present in the ACS %d response", zcta_code, dataset_year)
            ingestion_repo.record_issue(
                session,
                run,
                f"ZCTA {zcta_code} not returned by ACS {dataset_year}",
                kind="missing",
            )

    return written


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--year",
        type=int,
        default=acs.ACS_DATASET_YEAR,
        help=f"ACS 5-year dataset vintage (default: {acs.ACS_DATASET_YEAR})",
    )
    parser.add_argument("--region", default="atlanta", help="Region slug to attach ZCTAs to")
    parser.add_argument(
        "--city",
        default=None,
        help="City key in SUPPORTED_CITY_ZCTAS (defaults to --region)",
    )
    args = parser.parse_args(argv)
    city = args.city or args.region

    if city not in acs.SUPPORTED_CITY_ZCTAS:
        raise SystemExit(
            f"Unsupported city {city!r}. Known: "
            f"{', '.join(sorted(acs.SUPPORTED_CITY_ZCTAS))}.\n"
            f"Add its ZCTA list to SUPPORTED_CITY_ZCTAS in "
            f"app/services/socioeconomic_service.py first."
        )

    # The ACS vintage is baked into the URL in the service module, so a
    # --year that disagrees with it would fetch one year and label it
    # another.
    if args.year != acs.ACS_DATASET_YEAR:
        raise SystemExit(
            f"--year {args.year} does not match ACS_DATASET_YEAR "
            f"({acs.ACS_DATASET_YEAR}) in app/services/socioeconomic_service.py, "
            f"which is what determines the URL actually queried. Update that "
            f"constant instead so the fetch and the label cannot disagree."
        )

    with session_scope() as session:
        region = reference.get_region(session, args.region)
        source = reference.get_data_source(session, SOURCE_SLUG)
        with ingestion_repo.run_scope(
            session,
            "socioeconomic",
            region=region,
            source=source,
            params=vars(args),
        ) as run:
            written = collect(session, run, region, dataset_year=args.year, city=city)
            run.rows_written = written
            logger.info("Wrote %d socioeconomic records for %s %d", written, city, args.year)


if __name__ == "__main__":
    main()
