"""Download the four surface pollutants required by the risk model."""
import argparse
from dataclasses import replace
from pathlib import Path

from ingestion.geos_cf import fetch_analysis as analysis
from ingestion.geos_cf import fetch_forecast as forecast

POLLUTANTS = {"o3", "no2", "so2", "pm25"}


def download(start, end, output=Path("data/geos_cf")):
    window = forecast.TimeWindow(start, end)
    product = analysis.product_for([window])
    product = replace(
        product, specs=tuple(spec for spec in product.specs if spec.name in POLLUTANTS),
        unavailable=tuple(spec.name for spec in forecast.VARIABLES if spec.name not in POLLUTANTS),
    )
    return analysis.run(forecast.region_area("georgia"), window, output, product=product, sampling="risk")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    print(download(forecast.parse_time(args.start, "UTC"), forecast.parse_time(args.end, "UTC")))


if __name__ == "__main__":
    main()
