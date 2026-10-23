# EPA AirNow observations

Run from backend/:

```powershell
python -m ingestion.air_quality.collector --hours 24
python -m pytest tests/test_air_quality.py
```

Set AIRNOW_API_KEY in the environment or backend/.env. The optional dotenv
loader is included in requirements/ingestion.txt. The client and normalizer
otherwise use the standard library. The region file defaults to
backend/ingestion/regions/atlanta-metro.json; --region-file overrides it.

The collector archives raw responses plus request metadata under
backend/data/raw/air_quality/airnow/ and normalized records under
backend/data/normalized/air_quality/airnow/. --data-dir overrides the root.
Generated archives are ignored by Git. API keys are excluded from saved request
metadata. Raw output should still be reviewed before external sharing.

Normalization retains station identity/coordinates, pollutant, UTC observation
and retrieval times, concentration/unit, and pollutant AQI where available.
Nonfinite values and missing sentinels become missing. Duplicate station,
pollutant, and timestamp records collapse within one response. Consumers must
also deduplicate across archived collections.

There is no database writer, scheduler, or GCS adapter yet. Keep AQI distinct
from concentration, retain source units, and define conversion/quality rules
before joining this data to GEOS-CF or symptom exposures. The current archive
is a development collector, not a production shared history store.

Historical source notes and earlier live-run claims are preserved in
../../legacy/airnow_README.md. They were not independently revalidated during
this cleanup. The shipped metro box differs from the old raster extent, which
stops south of downtown Atlanta.
