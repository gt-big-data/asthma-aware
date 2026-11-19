# Seven-day environmental trends

The dashboard uses NASA GEOS-CF **analysis/replay**, never forecasts, risk scores,
or lagged risk-exposure features. It displays NO2, SO2, PM2.5, and O3 at the
selected dashboard coordinates (city, selected cell, manual point, or device
location). Chemistry is converted from mol/mol to ppb; PM2.5 remains µg/m³.
These are model-based analysis estimates at native 0.25° resolution, not direct
monitor observations or learned 1-km downscaled concentrations.

## Refresh and storage

Run from `backend/`:

```powershell
python -m scripts.publish_environmental_trends
```

This probes the NASA analysis chemistry inventory and downloads the current
seven-completed-calendar-day window plus any available hours today. It reuses
the existing analysis cloud/OPeNDAP ingestion and cache. It never requests
future hours or fills analysis gaps with forecasts. `--input <analysis.nc>` can
publish a validated recent local analysis file without a network download.
Forecast NetCDF inputs and incorrect units are rejected.

The regular `scripts.publish_dashboard_forecast` command also refreshes trends
after successfully publishing the forecast. Trends have an independent pointer,
so an older forecast run does not force the analysis window to use that run's
initialization time. A failed trends refresh keeps the last successful trends
publication and does not roll back a successfully published risk forecast.
The trends command can also run as an independent scheduled Cloud Run Job
using the existing publisher identity, bucket, and image; no model checkpoint
or database access is required. Schedule it at least daily. No new IAM role,
bucket, secret, database migration, or frontend environment variable is needed.

Local storage uses `ENVIRONMENTAL_TRENDS_PATH` (default
`data/dashboard/environmental-trends.json`) and an immutable compressed hourly
NPZ next to it. On GCS, `analysis-trends/current.json` points to
`analysis-trends/<uuid>/hourly.npz`, with a SHA-256 checksum, observed-through
hour and download time. The artifact uploads before a generation-guarded pointer
update; older analysis cannot replace newer analysis. The API has read-only
access, verifies the checksum, and keeps at most two downloaded archives cached.
All raw NASA requests and processing happen in the publisher, not in API requests.

## API and display rules

`GET /v1/dashboard/environmental-trends?lat=...&lon=...` is public, uses normal
CORS, and returns `Cache-Control: no-store`. Coordinates must lie in Georgia
and the downloaded analysis grid. The endpoint is independent of risk forecasts
and their ML availability.

The requested period is always the last seven **completed calendar days in
America/New_York**, ending yesterday, regardless of whether NASA has published
all of those hours. Daily means require every expected hour at the selected
source cell: 24 normally, 23/25 across DST. Missing or non-finite hours produce
null daily values and gaps, never zeros or interpolated lines.

Each card's headline is the most recent complete daily mean within that period,
with its actual date. Its percentage compares that mean with the first day's
mean only if both exist, the first is nonzero, and the dates differ. The chart
retains seven date positions and never bridges missing days. Tapping a card
shows all daily values and available/expected hour counts. Empty history shows
an explicit unavailable state, not sample data. The source-through timestamp,
analysis lag, and incomplete-day counts remain visible when analysis is delayed.
Selections and in-flight results are scoped to the dashboard location/account,
so an older request cannot replace a newly selected location's trends.
