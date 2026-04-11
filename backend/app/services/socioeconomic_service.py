import os
import httpx
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()

CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")
CENSUS_URL = "https://api.census.gov/data/2023/acs/acs5"

ATLANTA_ZCTAS = [
    "30303", "30305", "30306", "30307", "30308",
    "30309", "30310", "30311", "30312", "30314",
]
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

CURRENT_YEAR = 2026


def get_socioeconomic_data(city: str):
    zcta_list = ",".join(ATLANTA_ZCTAS)
    params = {
        "get": "B01001_001E,B25035_001E",
        "for": f"zip code tabulation area:{zcta_list}",
        "key": CENSUS_API_KEY,
    }

    try:
        response = httpx.get(CENSUS_URL, params=params, timeout=15.0)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Census API error: {str(e)}")

    raw = response.json()
    headers = raw[0]
    rows = raw[1:]
    col = {name: idx for idx, name in enumerate(headers)}

    results = []
    for row in rows:
        zcta = row[col["zip code tabulation area"]]
        population = int(row[col["B01001_001E"]] or 0)

        year_built = row[col["B25035_001E"]]
        house_age = (CURRENT_YEAR - int(year_built)) if year_built and year_built != "-666666666" else None

        area = ZCTA_LAND_AREA.get(zcta)
        density = round(population / area, 2) if population and area else None

        results.append({
            "zipcode": zcta,
            "population_density": density,
            "average_household_age": house_age,
        })

    return results