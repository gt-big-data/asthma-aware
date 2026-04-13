import json
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from app.constants import DEFAULT_CITY

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SAMPLE_DATA_PATH = DATA_DIR / "sample_socioeconomic_data.json"

ACS_DATASET_YEAR = 2023
CENSUS_URL = f"https://api.census.gov/data/{ACS_DATASET_YEAR}/acs/acs5"

# These are Atlanta-area ZCTAs we want in the API response. Some USPS ZIP codes
# do not map cleanly to Census ZCTAs, so this list is intentionally ZCTA-first.
SUPPORTED_CITY_ZCTAS = {
    "atlanta": (
        "30303",
        "30304",
        "30305",
        "30306",
        "30307",
        "30308",
        "30309",
        "30310",
        "30311",
        "30312",
        "30313",
        "30314",
        "30315",
        "30316",
        "30317",
        "30318",
        "30324",
        "30326",
        "30331",
        "30354",
        "30363",
    )
}

ZCTA_LAND_AREA = {
    "30303": 1.37,
    "30305": 3.82,
    "30306": 2.91,
    "30307": 3.10,
    "30308": 1.89,
    "30309": 2.24,
    "30310": 3.55,
    "30311": 4.88,
    "30312": 1.72,
    "30314": 3.21,
}

ACS_VARIABLES = {
    "population": "B01001_001E",
    "male_under_5": "B01001_003E",
    "male_5_to_9": "B01001_004E",
    "male_10_to_14": "B01001_005E",
    "male_15_to_17": "B01001_006E",
    "male_65_to_66": "B01001_020E",
    "male_67_to_69": "B01001_021E",
    "male_70_to_74": "B01001_022E",
    "male_75_to_79": "B01001_023E",
    "male_80_to_84": "B01001_024E",
    "male_85_plus": "B01001_025E",
    "female_under_5": "B01001_027E",
    "female_5_to_9": "B01001_028E",
    "female_10_to_14": "B01001_029E",
    "female_15_to_17": "B01001_030E",
    "female_65_to_66": "B01001_044E",
    "female_67_to_69": "B01001_045E",
    "female_70_to_74": "B01001_046E",
    "female_75_to_79": "B01001_047E",
    "female_80_to_84": "B01001_048E",
    "female_85_plus": "B01001_049E",
    "median_age": "B01002_001E",
    "median_year_built": "B25035_001E",
    "median_household_income": "B19013_001E",
    "median_gross_rent": "B25064_001E",
    "median_home_value": "B25077_001E",
    "poverty_universe": "B17001_001E",
    "poverty_below": "B17001_002E",
    "education_total": "B15003_001E",
    "bachelors": "B15003_022E",
    "masters": "B15003_023E",
    "professional": "B15003_024E",
    "doctorate": "B15003_025E",
    "households_total": "B08201_001E",
    "households_no_vehicle": "B08201_002E",
    "rent_burden_total": "B25070_001E",
    "rent_burden_50_plus": "B25070_010E",
    "occupants_per_room_total": "B25014_001E",
    "owner_1_01_to_1_50": "B25014_005E",
    "owner_1_51_to_2_00": "B25014_006E",
    "owner_2_01_plus": "B25014_007E",
    "renter_1_01_to_1_50": "B25014_011E",
    "renter_1_51_to_2_00": "B25014_012E",
    "renter_2_01_plus": "B25014_013E",
    "civilian_labor_force": "B23025_003E",
    "unemployed": "B23025_005E",
}

INVALID_CENSUS_VALUES = {"", None, "-666666666", "-222222222", "-999999999"}


def _normalize_city(city: str) -> str:
    normalized = city.strip().lower()
    if normalized not in SUPPORTED_CITY_ZCTAS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported city '{city}'. Supported cities: {', '.join(SUPPORTED_CITY_ZCTAS)}",
        )
    return normalized


def _parse_census_int(value: Optional[str]) -> Optional[int]:
    if value in INVALID_CENSUS_VALUES:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_census_float(value: Optional[str]) -> Optional[float]:
    if value in INVALID_CENSUS_VALUES:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_values(*values: Optional[int]) -> Optional[int]:
    present_values = [value for value in values if value is not None]
    if not present_values:
        return None
    return sum(present_values)


def _safe_rate(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return round((numerator / denominator) * 100, 2)


def _median_housing_age(median_year_built: Optional[int]) -> Optional[int]:
    if median_year_built is None:
        return None
    return max(datetime.now().year - median_year_built, 0)


def _load_sample_data(city: str) -> List[Dict[str, Any]]:
    if not SAMPLE_DATA_PATH.exists():
        raise HTTPException(
            status_code=502,
            detail="Socioeconomic data is unavailable because the Census API request failed and no sample data exists.",
        )

    with SAMPLE_DATA_PATH.open("r", encoding="utf-8") as file:
        sample = json.load(file)

    if not isinstance(sample, list):
        raise HTTPException(status_code=500, detail="Sample socioeconomic data must be a list.")

    requested_zctas = set(SUPPORTED_CITY_ZCTAS[city])
    filtered = [row for row in sample if row.get("zipcode") in requested_zctas]

    return sorted(filtered or sample, key=lambda item: item["zipcode"])


def _fetch_census_rows() -> List[List[str]]:
    variable_codes = ",".join(["NAME", *ACS_VARIABLES.values()])
    params = {
        "get": variable_codes,
        "for": "zip code tabulation area:*",
    }

    census_api_key = os.getenv("CENSUS_API_KEY")
    if census_api_key:
        params["key"] = census_api_key

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(CENSUS_URL, params=params)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Census API error: {exc}") from exc

    payload = response.json()
    if not isinstance(payload, list) or not payload:
        raise HTTPException(status_code=502, detail="Census API returned an invalid payload.")

    return payload


def _get_int(row: List[str], columns: Dict[str, int], key: str) -> Optional[int]:
    return _parse_census_int(row[columns[ACS_VARIABLES[key]]])


def _get_float(row: List[str], columns: Dict[str, int], key: str) -> Optional[float]:
    return _parse_census_float(row[columns[ACS_VARIABLES[key]]])


def _transform_rows(raw_rows: List[List[str]], city: str) -> List[Dict[str, Any]]:
    headers = raw_rows[0]
    rows = raw_rows[1:]
    columns = {name: index for index, name in enumerate(headers)}
    city_zctas = set(SUPPORTED_CITY_ZCTAS[city])
    results: List[Dict[str, Any]] = []

    for row in rows:
        zcta = row[columns["zip code tabulation area"]]
        if zcta not in city_zctas:
            continue

        population = _get_int(row, columns, "population")
        area_sq_miles = ZCTA_LAND_AREA.get(zcta)
        median_year_built = _get_int(row, columns, "median_year_built")

        children_under_18 = _sum_values(
            _get_int(row, columns, "male_under_5"),
            _get_int(row, columns, "male_5_to_9"),
            _get_int(row, columns, "male_10_to_14"),
            _get_int(row, columns, "male_15_to_17"),
            _get_int(row, columns, "female_under_5"),
            _get_int(row, columns, "female_5_to_9"),
            _get_int(row, columns, "female_10_to_14"),
            _get_int(row, columns, "female_15_to_17"),
        )

        seniors_65_plus = _sum_values(
            _get_int(row, columns, "male_65_to_66"),
            _get_int(row, columns, "male_67_to_69"),
            _get_int(row, columns, "male_70_to_74"),
            _get_int(row, columns, "male_75_to_79"),
            _get_int(row, columns, "male_80_to_84"),
            _get_int(row, columns, "male_85_plus"),
            _get_int(row, columns, "female_65_to_66"),
            _get_int(row, columns, "female_67_to_69"),
            _get_int(row, columns, "female_70_to_74"),
            _get_int(row, columns, "female_75_to_79"),
            _get_int(row, columns, "female_80_to_84"),
            _get_int(row, columns, "female_85_plus"),
        )

        higher_ed_count = _sum_values(
            _get_int(row, columns, "bachelors"),
            _get_int(row, columns, "masters"),
            _get_int(row, columns, "professional"),
            _get_int(row, columns, "doctorate"),
        )

        overcrowded_households = _sum_values(
            _get_int(row, columns, "owner_1_01_to_1_50"),
            _get_int(row, columns, "owner_1_51_to_2_00"),
            _get_int(row, columns, "owner_2_01_plus"),
            _get_int(row, columns, "renter_1_01_to_1_50"),
            _get_int(row, columns, "renter_1_51_to_2_00"),
            _get_int(row, columns, "renter_2_01_plus"),
        )

        results.append(
            {
                "zipcode": zcta,
                "area_sq_miles": area_sq_miles,
                "population": population,
                "population_density": round(population / area_sq_miles, 2)
                if population is not None and area_sq_miles
                else None,
                "median_age": _get_float(row, columns, "median_age"),
                "children_under_18_rate": _safe_rate(children_under_18, population),
                "seniors_65_plus_rate": _safe_rate(seniors_65_plus, population),
                "median_housing_age": _median_housing_age(median_year_built),
                "median_household_income": _get_int(row, columns, "median_household_income"),
                "median_gross_rent": _get_int(row, columns, "median_gross_rent"),
                "median_home_value": _get_int(row, columns, "median_home_value"),
                "poverty_rate": _safe_rate(
                    _get_int(row, columns, "poverty_below"),
                    _get_int(row, columns, "poverty_universe"),
                ),
                "bachelor_degree_or_higher_rate": _safe_rate(
                    higher_ed_count,
                    _get_int(row, columns, "education_total"),
                ),
                "no_vehicle_households_rate": _safe_rate(
                    _get_int(row, columns, "households_no_vehicle"),
                    _get_int(row, columns, "households_total"),
                ),
                "severe_rent_burden_rate": _safe_rate(
                    _get_int(row, columns, "rent_burden_50_plus"),
                    _get_int(row, columns, "rent_burden_total"),
                ),
                "overcrowded_housing_rate": _safe_rate(
                    overcrowded_households,
                    _get_int(row, columns, "occupants_per_room_total"),
                ),
                "unemployment_rate": _safe_rate(
                    _get_int(row, columns, "unemployed"),
                    _get_int(row, columns, "civilian_labor_force"),
                ),
            }
        )

    if not results:
        raise HTTPException(status_code=404, detail=f"No socioeconomic data found for city '{city}'.")

    return sorted(results, key=lambda item: item["zipcode"])


@lru_cache(maxsize=8)
def get_socioeconomic_data(city: str = DEFAULT_CITY) -> Dict[str, Any]:
    normalized_city = _normalize_city(city)

    try:
        zipcodes = _transform_rows(_fetch_census_rows(), normalized_city)
        source = f"us_census_acs5_{ACS_DATASET_YEAR}"
    except HTTPException as exc:
        if exc.status_code != 502:
            raise
        zipcodes = _load_sample_data(normalized_city)
        source = "sample_data_fallback"

    return {
        "city": normalized_city,
        "dataset_year": ACS_DATASET_YEAR,
        "source": source,
        "zipcodes": zipcodes,
    }
