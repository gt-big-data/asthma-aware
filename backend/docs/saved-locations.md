# Saved Georgia locations

Tap the dashboard location pill to select Current location, search a Georgia
city, or view a saved place. Cities use Census representative coordinates;
they are not an exact home/work address. A label such as Home or Work is optional.
Users may view a city without saving. Signed-in users can save up to ten unique
cities, rename them, or remove them. The saved list persists in the account
database and is available on other signed-in devices.

The selected city preference is remembered on the device using a key scoped to
the signed-in account (with a separate guest key). It is resolved through the
server catalog on reload. Changing accounts clears the previous account's
displayed selection while the new preference loads. Selecting a city refreshes
the gauge, hourly timeline, drivers and daily overview together; obsolete requests
are aborted. The dashboard selection does not change a symptom report's location.

## Geography and sources

`app/data/georgia_cities.json` bundles 676 Georgia places from the
[2025 Census Gazetteer](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_gaz_place_13.txt).
The file includes the original source URL, SHA-256 and vintage. Friendly display
names omit city/town/CDP and unified/consolidated government suffixes; canonical
seven-digit Census GEOIDs are retained. All IDs belong to Georgia (state FIPS 13).
Clients send an ID and optional label; the server owns names and coordinates.
It never accepts arbitrary coordinates or user IDs in a saved-location write.

`app/data/georgia_boundary.json` bundles a simplified Census TIGERweb Georgia
boundary (the full query URL is recorded in the file). The public coverage check
uses this polygon to distinguish Georgia from neighboring states, rather than
equating the model rectangle with the state. The boundary is generalized to
roughly 0.001 degrees, so it is not a survey-grade border determination.

Saved Georgia cities outside the current 0.01-degree model footprint, such as
Savannah, are allowed but marked as having no current forecast coverage. Selecting
one clears old risk data and shows an explanatory message; no Atlanta fallback
or invented score is shown. A device location outside Georgia is similarly
blocked. If device positioning is unavailable, Atlanta is an explicit fallback
with a visible notice. Environmental trend fixtures are labeled as sample data.

## API

Public, no external geocoder or API key required:

- `GET /v1/locations/cities?q=...`: up to 20 matching Georgia places, with
  `forecast_supported`; blank search suggests common cities.
- `GET /v1/locations/cities/{city_id}`: resolve one canonical Georgia place.
- `GET /v1/locations/coverage?lat=...&lon=...`: state and model coverage flags.

Authenticated with existing Supabase JWTs:

- `GET /v1/locations`: the caller's saved list.
- `PUT /v1/locations/{city_id}` with `{ "label": "Home" }` (or `{}`):
  retry-safe create/rename, returning the saved place. One record per user/city.
- `DELETE /v1/locations/{city_id}`: idempotent removal, returns 204.

Labels are trimmed, nonblank when supplied, and limited to 80 characters.
A profile-row lock serializes saves to enforce the ten-city limit. Reads/writes
filter by the verified user and are also protected by forced owner-scoped RLS.

## Database and Google Cloud

Migration `0004_saved_locations` creates only `app.saved_locations`, its constraints,
owner policy, and runtime-role grants. It does not modify symptom reports,
personal model weights, or Supabase-managed auth tables. The table stays private
and must not be added to the Supabase Data API's exposed schemas.

Apply reviewed Alembic migrations through the operator connection before deploying
the API image. Cloud Run continues using the restricted runtime database login
in Secret Manager. Both JSON catalogs are included by the existing Dockerfile's
`COPY app/ ./app/`. No GCS change, new service account, geocoding credential, or
frontend secret is needed. CORS now permits DELETE in addition to the existing
methods. The updated runtime preflight checks all seven private application tables.

Validation covers API authentication, canonical Georgia IDs, missing/unsupported
coverage, idempotent writes/deletes, label/limit validation, forced RLS, cross-user
isolation, account-scoped device keys, and existing symptom API regressions.
