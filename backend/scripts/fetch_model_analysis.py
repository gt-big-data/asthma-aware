"""Fetch actual historical chemistry and meteorology for model-driven risk."""
import argparse
from dataclasses import replace
from pathlib import Path

from ingestion.geos_cf import fetch_analysis as analysis, fetch_forecast as nasa

NEEDED = {"o3", "no2", "so2", "pm25", "air_temperature", "specific_humidity", "surface_pressure", "pbl_height"}


def download(start, end, output=Path("data/geos_cf"), *, no2_column=False):
    """no2_column adds GEOS-CF's tropcol_no2, which column-scaled NO2 risk inputs need."""
    window = nasa.TimeWindow(start, end)
    product = analysis.product_for([window])
    needed = NEEDED | ({"tropcol_no2"} if no2_column else set())
    product = replace(product, specs=tuple(s for s in product.specs if s.name in needed),
                      unavailable=tuple(s.name for s in nasa.VARIABLES if s.name not in needed))
    return analysis.run(nasa.region_area("georgia"), window, output, product=product, sampling="risk_models", workers=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--no2-column", action="store_true", help="also fetch tropcol_no2")
    args = parser.parse_args()
    print(download(nasa.parse_time(args.start, "UTC"), nasa.parse_time(args.end, "UTC"), no2_column=args.no2_column))
