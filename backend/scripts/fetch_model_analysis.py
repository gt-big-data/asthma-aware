"""Fetch actual historical chemistry and meteorology for model-driven risk."""
import argparse
from dataclasses import replace
from pathlib import Path

from ingestion.geos_cf import fetch_analysis as analysis, fetch_forecast as nasa


def download(start, end, output=Path("data/geos_cf")):
    window = nasa.TimeWindow(start, end)
    product = analysis.product_for([window])
    needed = {"o3", "no2", "so2", "pm25", "air_temperature", "specific_humidity", "surface_pressure", "pbl_height"}
    product = replace(product, specs=tuple(s for s in product.specs if s.name in needed),
                      unavailable=tuple(s.name for s in nasa.VARIABLES if s.name not in needed))
    return analysis.run(nasa.region_area("georgia"), window, output, product=product, sampling="risk_models", workers=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    print(download(nasa.parse_time(args.start, "UTC"), nasa.parse_time(args.end, "UTC")))
