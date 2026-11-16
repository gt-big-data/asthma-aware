# NASA ingestion and ML artifact integration

This implementation contains no ML inference, placeholder model, downscaling or
risk scoring. NASA ingestion and artifact delivery are operational code; test
fixtures exist only in automated tests. Prior uncommitted placeholder drafts are
preserved under `backend/forecast_pipeline/reference/*.historical.txt` as excluded,
non-executable reference text. Existing `ml-work` models/tests are untouched.

## Source and acquisition

The configured source is NASA's [GEOS-CF v2 OPeNDAP forecast catalog](https://opendap.nccs.nasa.gov/dods/gmao/geos-cf/v2/fcst).
The catalog was retrieved through a read-only web tool during implementation.
The actual Python/NetCDF data download could not be verified because outbound
socket access is denied by this environment. A web catalog response is not proof
that the forecast downloader or cloud upload has completed successfully.

The real batch uses existing `ingestion.geos_cf.fetch_forecast` acquisition:

- Discovers the newest complete daily 09Z run across AQC, MET and XGC collections;
  uses dated URLs so collections cannot mix runs during alias advancement.
- Defaults to Atlanta: 33.45..34.05 latitude, -84.80..-83.95 longitude. Bbox can be
  explicitly overridden within the existing Georgia bounds; native grid is 0.25°.
- Fetches all variables from the existing NASA ingestion contract, including
  surface pollutants, meteorology and column products. No surface concentrations
  are relabeled as column products and units remain NASA's validated native units.
- Requests a bounded 1..120-hour horizon from initialization, default 48h. Hourly
  means retain midpoint stamps in NetCDF; manifest validity spans each averaging
  window (first midpoint minus 30min, last midpoint plus 30min).
- Limits per-request subprocess timeout to 20s and retries to two attempts with
  2s backoff. The outer process deadline defaults to 240s, beneath Cloud Run's 300s.
- Validates run/units/variable names/source mappings, UTC timestamps, coordinate
  order and bounds, missing/negative surface pollutants, full finite data and
  metadata/NetCDF agreement. Rejects future or >48h-old initializations. No fill,
  interpolation, adjustment, synthetic fallback or model function executes.
- Bounds the request at 1,000 native cells and 64MiB per object. Current Atlanta
  subset is small; full regional/horizon memory must be measured before expanding.

## Running locally or in Cloud Run

From `backend/`, with NASA and cloud storage requirements installed:

```powershell
python -m scripts.publish_nasa_forecast --output-dir data/nasa --hours 48 --deadline 240
```

Local mode uses a transactional SQLite artifact store at `data/nasa/catalog` and
keeps the actual acquired `.nc` and `.metadata.json` on disk. This SQLite store is
only a local replacement for object storage, not the Supabase application database.

Cloud Run attaches an authorized runtime service account and sets:

```text
NASA_RAW_BUCKET=<private raw bucket>
NASA_OUTPUT_DIR=/tmp/nasa
```

Or pass `--gcs-bucket` explicitly. Credentials never belong in command arguments.
`--input <actual NASA .nc>` accepts an already-acquired product with its real NASA
sidecar, validates it fully, and publishes it without another NASA request.
`--run YYYY-MM-DD` pins a run, but freshness checks still reject stale files.

Local complete products are reused before transferring native fields again. A
fresh ephemeral Cloud Run task can restore an already-published immutable product
from GCS using its request fingerprint, avoiding repeated NASA field downloads.
Latest-run discovery still checks the source. Scientific content hashing ignores
NetCDF serialization/download-time differences; a different scientific product
under the same run/request identity is rejected rather than silently overwritten.

## Raw object contract

```text
raw/<YYYYMMDD_09z>/<request-fingerprint>/forecast-<bytes-sha256>.nc
raw/<YYYYMMDD_09z>/<request-fingerprint>/metadata-<bytes-sha256>.json
raw/<YYYYMMDD_09z>/<request-fingerprint>/manifest.json
raw/current.json
```

Raw manifest schema is `nasa-geos-cf-raw-v1`, with source, run identity, artifact
version, initialization, validity interval, covered native-cell footprints,
variable→unit mapping and pinned object references. Each reference records `name`,
`generation`, `sha256`, `size_bytes`, `content_type`. Manifest carries
`ml_status: pending`; no output or prediction is invented when models are absent.

Creation is immutable and generation-preconditioned. All files are read back and
verified before the manifest can become current. The pointer is atomically
promoted via compare-and-swap with up to three conflict retries. Older runs cannot
regress it. Duplicate delivery is a no-op. A different same-run snapshot requires
an explicit release decision; it does not overwrite an already-selected run.
Interrupted uploads leave no selected incomplete product. Do not delete retained
objects while manifests reference their generations.

Raw filenames are byte-addressed so an orphan from an interrupted task cannot
block a later complete publication whose identical scientific values were
serialized with a newer download timestamp. The stable request/version manifest
identity and scientific-content deduplication remain unchanged. An old orphan
is never selected merely because its filename exists.

## ML team handoff: supplied output only

The ML team owns predictor normalization, quantity-specific baseline alignment,
model loading/inference and the actual forecast outputs. This backend accepts
actual output files and provenance using `asthma-ml-output-v1`. It never calls a
model callback and never creates a score/tile if none has been supplied.

The output manifest has these fields:

```text
schema_version: asthma-ml-output-v1
product: processed
source: NASA GEOS-CF v2
run_id, artifact_version, initialized_at, valid_from, valid_until
bounds: {min_lat,max_lat,min_lon,max_lon}
variables: {<actual_output_quantity>: <explicit_unit>}
audience: environmental-public
contains_user_data: false
model: {name,version,input_contract: nasa-geos-cf-raw-v1}
input_raw_manifest: {name,generation,sha256,size_bytes,content_type}
files: [{name,relative_path,sha256,size_bytes,content_type}]
```

`relative_path` is a local staging input only. Names must be under
`processed/<run_id>/<artifact_version>/`; paths cannot escape the supplied root.
Allowed MIME types: JSON, NetCDF, PNG, WebP and Mapbox Vector Tile. Unknown manifest
fields are rejected. Actual raw manifest bytes/generation/checksum and source run
are verified; output time/geography cannot exceed its input raw coverage. Required
source fields carry units unchanged; column vs surface targets remain distinct.

```powershell
python -m scripts.publish_processed_forecast `
  --manifest actual-ml-output-manifest.json --files-root actual-output-files `
  --raw-bucket PRIVATE_RAW --processed-bucket PRIVATE_STAGING `
  --delivery-bucket ENVIRONMENTAL_DELIVERY --deadline 240
```

This command requires actual supplied files. It stages verified immutable objects
in the processed bucket, then copies/rechecks them into the separate CDN delivery
bucket. Object generations are rebuilt separately for each bucket. Only after all
delivery files pass checksum verification does `processed/current.json` advance.
Personal records never belong in the environmental delivery bucket. Raw NetCDF is
not automatically copied into that bucket.

When no ML output has been submitted, `ForecastCatalog.availability()` reports
processed `pending` and `ml_status: pending`; metadata/delivery cannot pretend an
ML forecast exists. Empty or invalid output is unavailable, not zero risk.

## Backend integration API

`ForecastCatalog(store).availability(now=None)` returns raw/processed status and
run/validity fields. `metadata(product='processed', now=None)` returns a complete
manifest or raises `ProductUnavailable`; malformed/inconsistent data raises
`CatalogError`. `delivery(name, now=None)` returns `(bytes, content_type)` only for
an exact file in the currently valid processed manifest. The production backend
uses separate catalogs for private raw and environmental delivery buckets.

Cloud CDN signing/HTTP routing is outside this module. Callers must authorize the
request and select an exact manifest reference before signing an immutable path.
No personal data or private raw object may become a public cache response.

## Verification boundaries

Automated tests exercise actual NetCDF serialization/validation, cache reuse,
local transactional object generation checks, raw and processed publication,
cross-bucket reference rebuilding, manifest integrity, interrupted publication,
invalid/private/path-escaping supplied outputs and CLI deadline cleanup. Provider
and GCS boundaries are explicit test doubles. Live NASA binary acquisition, GCS
upload and Cloud Run execution remain unverified; no paid or production operation
was performed by this implementation.
