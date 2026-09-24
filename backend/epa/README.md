# AsthmaAware — Deliverable 2: EPA AirNow Air Quality Observations

A Python pipeline that retrieves recent air-quality observations from EPA AirNow monitoring
stations inside a geographic bounding box, archives the provider response unchanged, and writes
a standardized JSON representation for the AsthmaAware data and ML pipeline.

Indicators collected: **PM2.5**, **Ozone (O3)**, **NO2**, and the **AQI** reported for each.

> **Important — the deliverable's exact bounding box was dropped.** The coordinates given in the
> deliverable (33.39–33.64 N, −84.55 to −84.28 W) contain no AirNow monitors — a live run against
> them returned zero observations, confirmed on 2026-09-15. That narrow region file has been
> removed. `regions/atlanta-metro.json`, a wider box that does contain real stations, is now the
> only and default region. See [Geographic region](#geographic-region) for the numbers and
> [Known limitations](#known-limitations) for why the original box was empty.

---

## Data product and endpoint

The collector uses a single AirNow product: the **Air Quality Data API**.

| | |
|---|---|
| Endpoint | `https://www.airnowapi.org/aq/data/` |
| Docs | https://docs.airnowapi.org/Data/docs (requires a free AirNow account login) |
| Returns | Hourly monitor-level observations for every station inside a bounding box |

This endpoint was chosen over the `/aq/observation/latLong/` endpoints because those return
*reporting-area* summaries (a city-level rollup) rather than individual monitoring stations.
Deliverable 2 requires station identity and coordinates to be preserved, so station-level data
is required.

Request parameters sent on every call:

| Parameter | Value | Meaning |
|---|---|---|
| `startDate` / `endDate` | `YYYY-MM-DDTHH` (UTC) | Collection window, hour granularity |
| `parameters` | `PM25,OZONE,NO2` | Pollutants requested |
| `BBOX` | `minLon,minLat,maxLon,maxLat` | Study-area boundary (note the lon/lat order) |
| `dataType` | `B` | "Both" — return concentration **and** AQI in one response |
| `format` | `application/json` | |
| `verbose` | `1` | Adds `SiteName`, `AgencyName`, `FullAQSCode`, `IntlAQSCode` |
| `monitorType` | `2` | Permanent and mobile monitors |
| `includerawconcentrations` | `0` | Leave out the extra `RawConcentration` field (not used by the normalized schema) |

## API access

AirNow requires a free API key.

1. Request one at https://docs.airnowapi.org/account/request/ (approval is immediate).
2. Make it available to the collector in either of two ways:

```bash
export AIRNOW_API_KEY="your-key-here"          # preferred
python -m ingestion.air_quality.collector --api-key your-key-here   # or pass it explicitly
```

The key is never written to the archive: the request metadata sidecar stores the query string
with `API_KEY` removed.

Rate limit: 500 requests/hour per key. The collector makes **one** request per run, so a run
every few minutes is well within the limit.

## How to run

Requires Python 3.10+. There are no third-party dependencies — everything uses the standard
library, so no virtualenv or `pip install` is strictly necessary.

```bash
cd backend/epa
export AIRNOW_API_KEY="your-key-here"

# Default: the atlanta-metro region, previous 24 hours
python -m ingestion.air_quality.collector --hours 24

# Shorter window
python -m ingestion.air_quality.collector --hours 3

# An explicitly-chosen region file (same effect as the default, spelled out)
python -m ingestion.air_quality.collector --hours 24 --region-file regions/atlanta-metro.json
```

Options:

| Flag | Default | Description |
|---|---|---|
| `--hours` | `24` | Number of hourly observation slots to collect, ending at the current UTC hour |
| `--region-file` | `regions/atlanta-metro.json` | JSON bounding box to collect |
| `--data-dir` | `data/` | Root of the data archive |
| `--api-key` | `$AIRNOW_API_KEY` | AirNow API key |

Exit codes: `0` success, `1` AirNow request failed, `2` configuration error (missing key or
invalid region file).

Run from `backend/epa/`, otherwise Python can't find the `ingestion` package. On macOS with the
python.org installer, use `python3` if `python` isn't found.

### Tests

The tests use only the standard library and never call AirNow, so no API key is needed:

```bash
cd backend/epa
python3 -m unittest discover tests
```

## Geographic region

The region is supplied as a JSON file so the pipeline is not hard-coded to a single box:

```json
{
  "region": "atlanta-metro",
  "min_lat": 33.30,
  "max_lat": 34.10,
  "min_lon": -84.90,
  "max_lon": -83.90
}
```

This is `regions/atlanta-metro.json`, the collector's only and default region file. It covers
the metro area including the city core and northern suburbs, and it's what every example output
in this README comes from (9 stations, 300+ records per 24-hour run).

**This is not the exact box given in the deliverable.** The deliverable's coordinates
(33.39–33.64 N, −84.55 to −84.28 W) cover the south metro — the airport, College Park,
Jonesboro — but stop short of the city core where Atlanta's regulatory monitors actually sit. A
live 24-hour run against those exact coordinates returned **zero observations**, confirmed on
2026-09-15, so that region file was removed rather than kept as a non-working default. See
[Known limitations](#known-limitations) for the station-by-station detail. If a future need
arises to reproduce the deliverable's literal box, recreate a region file with those coordinates
and pass it with `--region-file`; it will run without errors, it will just return no records.

The bounding box is enforced twice: AirNow filters server-side via `BBOX`, and every returned
observation is re-checked against the box during normalization. Anything outside is dropped and
counted in the run summary.

## Time window

- The window is evaluated in **UTC** and made of whole hours.
- It ends at the current UTC hour and starts `hours − 1` hours earlier. AirNow treats both
  `startDate` and `endDate` as inclusive, so `--hours 24` requests exactly 24 hourly slots. For
  example, a run at `2026-09-15T17:19Z` requests `2026-09-14T18` through `2026-09-15T17`.
- Each returned observation is an **hourly average labeled by the beginning of the hour**. An
  observation with `observed_at` of `2026-09-10T16:00:00Z` covers 16:00–17:00 UTC.

## Timestamps

| Field | Meaning |
|---|---|
| `observed_at` | Start of the hour the measurement covers, from AirNow's `UTC` field |
| `retrieved_at` | When the collector made the request |

Both are **UTC, ISO 8601, with an explicit `Z` suffix** (`2026-09-10T16:00:00Z`). AirNow returns
its `UTC` field without a timezone designator (`2026-09-10T16:00`); the collector attaches UTC
explicitly and normalizes to full second precision so downstream consumers never have to guess.

No local-time field is stored. Atlanta is UTC−4 (EDT) or UTC−5 (EST); convert at read time
using `America/New_York` rather than a fixed offset so DST is handled correctly.

## Output schema

One record per **station + pollutant + observation hour**.

```json
{
  "source": "airnow",
  "station_id": "131210055",
  "station_name": "United Ave",
  "latitude": 33.7206,
  "longitude": -84.3578,
  "observed_at": "2026-09-14T17:00:00Z",
  "retrieved_at": "2026-09-15T17:19:26Z",
  "pollutant": "pm25",
  "value": 4.4,
  "unit": "ug/m3",
  "aqi": 24
}
```

| Field | Type | Notes |
|---|---|---|
| `source` | string | Always `"airnow"` |
| `station_id` | string | AirNow `FullAQSCode`, the 9-digit EPA AQS site code (`SSCCCNNNN`: state, county, site). Falls back to `IntlAQSCode` (same code with the `840` USA prefix), then to a coordinate-derived `airnow-<lat>-<lon>` id |
| `station_name` | string \| null | AirNow `SiteName` |
| `latitude` / `longitude` | number | Station coordinates, WGS84 decimal degrees |
| `observed_at` | string | UTC ISO 8601, start of the observation hour |
| `retrieved_at` | string | UTC ISO 8601, time of collection |
| `pollutant` | string | `pm25`, `o3`, or `no2` |
| `value` | number \| null | Pollutant concentration |
| `unit` | string \| null | Unit of `value` |
| `aqi` | integer \| null | AQI for this pollutant at this station and hour |

### Pollutant and unit mapping

| AirNow `Parameter` | `pollutant` | `unit` | Typical range |
|---|---|---|---|
| `PM2.5` | `pm25` | `ug/m3` | 0–200+ |
| `OZONE` | `o3` | `ppb` | 0–120 |
| `NO2` | `no2` | `ppb` | 0–100 |

AirNow reports ozone and NO2 in **parts per billion** on this endpoint, not ppm. PM2.5 is in
micrograms per cubic meter. The raw `UG/M3` / `PPB` unit strings are lowercased to `ug/m3` /
`ppb`; the value itself is never rescaled.

### AQI

`aqi` is the sub-index **for that pollutant at that station and hour** — not a composite. The
overall AQI for an area is the maximum sub-index across pollutants, which downstream code should
compute from these records rather than expecting it in the feed.

AQI is an integer on the standard EPA 0–500 scale: 0–50 Good, 51–100 Moderate, 101–150 Unhealthy
for Sensitive Groups, 151–200 Unhealthy, 201–300 Very Unhealthy, 301+ Hazardous.

### Missing data

AirNow uses **`-999`** as its missing-data sentinel. The collector converts it to `null` in both
`value` and `aqi`. A record is kept when either field is present — an NO2 concentration with no
AQI is still a usable observation — and dropped only when both are missing.

## File layout

The data folders are committed (via `.gitkeep` files) but the collected JSON files are
git-ignored by `backend/epa/.gitignore`.

```
data/
├── raw/air_quality/airnow/
│   ├── atlanta-metro_20260915T224217Z.json        # AirNow response body, byte-for-byte
│   └── atlanta-metro_20260915T224217Z.meta.json   # request params, window, bbox, retrieved_at
└── normalized/air_quality/airnow/
    └── atlanta-metro_20260915T224217Z.json        # JSON array of normalized records
```

- **Raw:** `data/raw/air_quality/airnow/` — the provider response is written unmodified. The
  sidecar `.meta.json` records what was requested so a run can be reproduced or audited without
  touching the raw file.
- **Normalized:** `data/normalized/air_quality/airnow/` — a JSON array of the records described
  above, sorted by `observed_at`, then `station_id`, then `pollutant`.

Filenames are `<region>_<retrieved_at>.json`, so runs never overwrite each other and raw and
normalized files from the same run share a stamp.

## Code layout

| File | Responsibility |
|---|---|
| [ingestion/air_quality/collector.py](ingestion/air_quality/collector.py) | CLI, region loading and validation, file archiving |
| [ingestion/air_quality/airnow_client.py](ingestion/air_quality/airnow_client.py) | AirNow HTTP request, retries, error detection |
| [ingestion/air_quality/normalize.py](ingestion/air_quality/normalize.py) | Mapping to the standardized record schema |
| [tests/test_air_quality.py](tests/test_air_quality.py) | Unit and end-to-end tests (no network) |

## Known limitations

- **The deliverable's specified bounding box contains no monitors and was dropped.** That box
  (33.39–33.64 N) covers the south metro — the airport, College Park, Jonesboro — but stops
  short of the city core. Atlanta's main regulatory monitors sit north of it: South DeKalb
  (≈33.69 N), NR-285 (≈33.70 N), United Ave (≈33.72 N), and NR-Georgia Tech (≈33.78 N).
  The example record in the deliverable is itself at latitude 33.78, outside its own box. A
  24-hour run against those exact coordinates on 2026-09-15 returned zero observations, so the
  region file was removed rather than shipped as a non-working default; `regions/atlanta-metro.json`
  (344 records from 9 stations on the same day) is the only region file now. This is a
  specification question worth resolving with the team if the exact deliverable box matters.
- **NO2 coverage is sparse.** Fewer stations report NO2 than PM2.5 or ozone. The same 2026-09-15
  metro run had 66 NO2 records against 160 ozone and 118 PM2.5. If AirNow omits the AQI for an
  observation, the record is kept with `"aqi": null`.
- **Ozone is seasonal.** Many Georgia ozone monitors run only during the ozone season
  (roughly March–October), so winter collections will return little or no O3 data.
- **Reporting lag.** Observations typically appear 1–2 hours after the hour they describe. The
  most recent hour in a window is often absent; it usually fills in on a later run.
- **Data is preliminary.** AirNow publishes real-time data that has not passed final QC. Values
  can be revised or withdrawn later and do not match EPA's archived AQS data exactly. Do not
  treat this feed as the regulatory record.
- **Late-arriving data is not backfilled automatically.** Each run is an independent snapshot.
  Overlapping windows across runs are expected and deduplicated *within* a run, but not across
  runs — whichever consumer merges these files should dedupe on
  `(station_id, pollutant, observed_at)`, preferring the later `retrieved_at`.
- **Mobile monitors may lack a stable ID.** When AirNow returns no `FullAQSCode` or `IntlAQSCode`,
  the collector synthesizes an id from coordinates. These ids are not stable if the unit moves.
- **Single request per run.** Very large bounding boxes or long windows can exceed AirNow's
  response limits. For wide-area or multi-day backfills the window should be chunked.
