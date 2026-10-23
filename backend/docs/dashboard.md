# Dashboard demo contract

The only active app route is GET /dashboard?lat=...&lon=....
Run the API from backend/ with python -m uvicorn app.main:app --reload.
Set DASHBOARD_DEMO_ENABLED=true explicitly to serve fixture responses.
Without that flag the endpoint returns 503. Authentication is not implemented.
Successful demo responses include X-AsthmaAware-Data-Source: demo.

## Current response

| Field | Meaning in the demo |
| --- | --- |
| location | Submitted coordinates and coarse Georgia fixture cell ID |
| forecast | Synthetic run ID and current UTC valid hour |
| risk | Placeholder 0-100 score and low/moderate/high category |
| contributors | Positive personal correction shares only; not full attribution |
| hourly_risk | Atlanta local calendar day, including 23/25-hour DST days |

Coordinates are validated; unsupported geography returns 404. Identity comes
from an overridable demo dependency, never a request-supplied user ID.
The default fixture profile is replayed for cells without their own profile.
This is fabricated demo coverage, not live environmental coverage.

The service computes severity = g + sum(weight * standardized_feature), then
uses score = round(severity / 4 * 100), clamped to 0-100. Category thresholds
are 40 and 70. The scale and thresholds are illustrative and unvalidated.
Missing user weights default to zero in this prototype; production cold start
and version mismatch policies remain undecided.

The grid is a 0.05-degree rectangle covering Georgia; it is unrelated to the
historical raster grid or the planned Atlanta 500 m forecast grid. Run identity
is synthesized from local midnight, not from an actual forecast publication.

## Next integration

Replace fixture loaders with versioned forecast and personalization providers,
verify Supabase identity, define unavailable/stale/version-mismatch behavior,
and agree on full-model attribution and curve semantics. Keep model corrections
on the severity scale before converting to a display score.

The older deliverable document is preserved in ../legacy/dashboard_README.md.
It is historical context, not evidence of implemented authentication or finalized
scientific/product choices.
