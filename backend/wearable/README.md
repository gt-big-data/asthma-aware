# AsthmaAware — Wearable health data (`/wearable`)

The backend for the smartwatch layer of the early-warning system. It takes
daily metrics from a patient's watch and phone and turns them into the one
thing the environmental model cannot provide: **how this particular body is
responding**.

- **`POST /wearable/samples`** — a device syncs its daily metrics.
- **`GET /wearable/metrics`** — the latest health data, each number next to that user's own baseline.
- **`GET /wearable/body-response`** — a single 0–100 score for how far their physiology has drifted from normal, plus what is driving it.
- **`GET /wearable/trends/{metric}`** — one metric's recent series with its baseline band, for charting.
- **`GET /wearable/catalog`** — which metrics the system accepts, with units and plausible ranges.

The analysis is real and final. What is still stood in for is the data
source (no watches are paired yet) and the storage engine.

| Piece | Status now | Replaced later by |
|---|---|---|
| Baselines, deviations, drift detection | **Final** | — |
| Body-response score, contributors, coverage | **Final** | — |
| Ingest validation, idempotent upsert | **Final** | — |
| Device data | Generated demo history (`app/services/demo_history.py`) | Real syncs from the Samsung Health SDK / HealthKit |
| Cough counts | Part of the same demo history | The HeAR-based nocturnal cough detector |
| Storage | JSON file (`app/services/sample_store.py`) | Postgres via `backend/db/` |
| Metric weights | Priors graded by strength of evidence | Weights fitted on AAMOS-00 |
| Score scale and level bands | Placeholder constants | Calibrated against labelled exacerbations |
| Signed-in user | Always `demo-user` | Authentication |

This folder is self-contained. It does not import from `backend/app/`,
`backend/db/` or `backend/dashboard/`.

---

## Why a watch at all

Two people breathing the same air have very different days. The
environmental model knows what a user is *exposed* to; it cannot know that
one of them has been sleeping badly for three nights and is coughing more.
That is the gap this service fills, and the reason it has to be passive:
peak-flow diaries and symptom logs depend on the patient remembering, and
adherence collapses exactly when people feel well.

The design rests on one finding. The FACET study of 425 severe
exacerbations found that symptoms and lung function **worsen gradually
over several days** before an attack peaks. That buildup is the prediction
window — but only for a system that measures the user every day. Hence
daily metrics, a personal baseline, and drift detection over a few days
rather than an alarm on a single bad night.

Full background and the paper list are in `asthma_wearable_cough_writeup.md`
on [PR #11](https://github.com/gt-big-data/asthma-aware/pull/11), which is
the spec this deliverable implements. (Not yet merged, so there is no
in-repo link to it from this branch.)

---

## Running it

Requires Python 3.11+. From `backend/wearable/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8100
```

The demo user is seeded on first start, so the read endpoints work straight
away:

- <http://127.0.0.1:8100/wearable/metrics> — latest metrics vs baseline
- <http://127.0.0.1:8100/wearable/body-response> — the score and what is driving it
- <http://127.0.0.1:8100/wearable/trends/nocturnal_cough_count?days=14>
- <http://127.0.0.1:8100/docs> — interactive API docs

Port 8100 keeps it clear of `backend/app` and `backend/dashboard`, which
both default to 8000.

### Tests

```bash
python -m pytest
```

128 tests, no network, no API keys, no database. They run against an
in-memory store, so they never touch your local `wearable_store.json`.

---

## The core idea: compare a user with themselves

A resting heart rate of 72 bpm is not information. It is unremarkable for
one person and five beats above normal for another. So every number this
service reports is a **deviation from that user's own recent history**,
measured in units of their own day-to-day variability:

```text
deviation = adverse_direction x (value - personal_median) / personal_spread
```

- `0` is a typical day for them.
- `+2` is two of their own standard deviations **in the worse direction**.
- Negative is better than their normal.

`adverse_direction` flips the sign for metrics where *lower* is worse (HRV,
SpO2, sleep, steps), so a positive deviation always means "worse" whatever
the metric. Without it, a rising heart rate averaged against a falling HRV
would cancel two warning signs into zero.

### Three deliberate choices

**Median and MAD, not mean and standard deviation.** One bad night — a
fever, a late flight, a toddler — shifts a 28-day mean noticeably and
inflates its standard deviation, which then hides the very drift it should
reveal. The median and median absolute deviation barely move for a handful
of outliers. Given that the outliers *are* the events we are trying to
detect, that robustness is the whole point.

**The baseline excludes the day being judged.** The window is the 28 days
*before* the target date. Including the day would let an extreme value pull
the median toward itself and understate its own deviation — the signal
partly erasing itself.

**A minimum history before claiming anything.** Under 7 days the spread
estimate is too noisy to call anything unusual, so the baseline is reported
as `baseline_pending` rather than guessed at. A new user sees their real
numbers from day one and deviations from their second week.

### The spread floor

A user with a very steady metric can have a MAD of exactly zero — a cough
count of 0 every night, for instance — which would make every deviation
infinite. Each metric therefore has a `min_spread` floor, set to roughly
its sensor's own measurement noise, so "quieter than the sensor can
resolve" is treated as sensor-noise-sized rather than as zero.

---

## Metrics

Nine daily summaries. That granularity is what consumer watch SDKs
actually expose (Samsung Health, HealthKit and Fitbit all surface nightly
resting heart rate, HRV and sleep rather than raw beat-to-beat series), and
it is the granularity the science is about.

| Metric | Unit | Worse when | Weight | Source |
|---|---|---|---|---|
| `resting_heart_rate` | bpm | higher | 0.20 | watch |
| `nocturnal_cough_count` | coughs/night | higher | 0.20 | phone |
| `hrv_rmssd` | ms | lower | 0.15 | watch |
| `respiratory_rate` | breaths/min | higher | 0.15 | watch |
| `spo2` | % | lower | 0.10 | watch |
| `sleep_efficiency` | % | lower | 0.08 | watch |
| `sleep_duration` | hours | lower | 0.05 | watch |
| `steps` | steps | lower | 0.04 | watch |
| `skin_temperature` | degC | higher | 0.03 | watch |

Weights are graded by how strongly the literature supports each signal.
Resting heart rate and cough are the two markers the 2024 *ERJ Open
Research* review of digital markers of asthma exacerbations called most
promising, so they carry the most; HRV (Rahman et al. 2019) and nighttime
respiratory rate follow; sleep, activity and skin temperature are
supporting context. **These are priors, not fitted values** — the first
thing to replace once there is labelled data to fit against.

Adding a sensor means adding a row to
[`app/services/metrics_catalog.py`](app/services/metrics_catalog.py) (and
demo values in `demo_history.py`). Nothing else needs to change.

---

## The body-response score

```text
body_response = sum(w_i x d_i) / sum(w_i)    over metrics with a baseline
```

Then `score = round(body_response / 3.0 x 100)`, clamped to 0–100: a
typical day is `0`, a weighted drift of three personal sigmas is `100`.
Levels are `low` (0–39), `moderate` (40–69), `high` (70–100) — the same
bands as `GET /dashboard`, so they mean the same thing wherever the app
shows them.

Two properties worth knowing:

**Missing sensors do not dilute the score.** The division renormalises over
whatever actually reported. A watch with no SpO2 sensor gets a score built
from the rest, rather than one quietly dragged toward zero by a missing
term.

**So every response carries `coverage`.** A score from two metrics is a
much weaker claim than one from nine, and `weight_available` says which you
are looking at — the share of total catalog weight that had both a value
and an established baseline. `sufficient` is false below 0.40. A client
that shows the score without the coverage is overstating it.

**A user with no baseline gets `body_response: null`, not `0`.** Zero would
read as "doing fine", which is not a claim the service can make about
someone it has no history for.

### Drift

The signal this service exists for is not "today is bad" but **"today is
the third bad day in a row"**. `drift` lists metrics at least 1.0 personal
sigma into the adverse side on 3 or more consecutive days, looking back up
to 7.

Each day is judged against *its own* trailing baseline, so a run that
merely reflects a slowly shifting baseline does not register. A missing day
breaks the run rather than extending it: treating "unknown" as "adverse"
would turn a night on the charger into a warning.

A permanently high resting heart rate is therefore **not** a drift. A high
baseline is not a deterioration; only departure from it is.

---

## Request and response details

### `POST /wearable/samples`

```json
{
  "device_id": "galaxy-watch-7",
  "samples": [
    { "metric": "resting_heart_rate", "date": "2026-10-15", "value": 69 },
    { "metric": "nocturnal_cough_count", "date": "2026-10-15", "value": 19 }
  ]
}
```

**`date` is the local calendar date the measurement belongs to.** For
overnight metrics (sleep, resting heart rate, nocturnal cough) that is the
*morning the night ended*: a night from the 14th into the 15th is dated the
15th. Devices disagree about this, and getting it wrong puts every
overnight metric one day out of step with the daytime ones.

**Syncs are idempotent per `(metric, date)`.** Re-sending a day overwrites
it. A device can safely retry a sync whose response was lost, and can
correct itself — watches routinely revise overnight metrics a few hours
later once sleep-stage analysis finishes.

**Partial acceptance.** A batch of 50 samples with one bad value stores the
other 49 and reports what it dropped:

```json
{
  "accepted": 1,
  "rejected": [
    {
      "index": 1,
      "metric": "spo2",
      "date": "2026-10-15",
      "reason": "value_out_of_plausible_range",
      "expected_range": [70.0, 100.0],
      "unit": "%"
    }
  ],
  "stored_dates": ["2026-10-15"],
  "metrics_stored": ["resting_heart_rate"],
  "last_sync_at": "2026-10-16T00:16:18Z"
}
```

Rejecting the whole batch would mean one faulty sensor silently costs the
user every other metric — and because syncs retry with the same payload, it
would cost them those metrics permanently. So the status is `200` even when
samples were dropped: **check `rejected`, not just the status code.**

Rejection reasons:

| `reason` | When |
|---|---|
| `unknown_metric` | Not in the catalog |
| `value_not_finite` | `NaN` or infinity |
| `value_out_of_plausible_range` | Outside the metric's physiological range |
| `date_in_the_future` | More than 1 day ahead of today in Atlanta (1 day of slack for devices in eastern timezones) |
| `date_too_far_in_the_past` | Over 400 days old — a device clock problem, not history |

Values are filtered at the door because this service's entire output is a
comparison against a four-week median: one impossible value distorts the
user's baseline for a month. A dropped PPG contact reports 0 bpm, and a
watch left on a bedside table reports a skin temperature of 21 degC.

### `GET /wearable/metrics`

```json
{
  "user_id": "demo-user",
  "as_of": "2026-10-15",
  "device": { "device_id": "galaxy-watch-7", "last_sync_at": "2026-10-16T00:16:18Z" },
  "metrics": [
    {
      "metric": "resting_heart_rate",
      "display_name": "Resting heart rate",
      "unit": "bpm",
      "source": "watch",
      "adverse_direction": "higher",
      "value": 66.0,
      "baseline": { "median": 62.0, "spread": 1.5, "days_used": 28, "window_days": 28 },
      "deviation": 2.67,
      "status": "concern",
      "description": "Lowest sustained heart rate overnight. Drifts upward days before an exacerbation."
    }
  ],
  "missing_metrics": [],
  "coverage": { "metrics_reported": 9, "metrics_scored": 9, "metrics_expected": 9,
                "weight_available": 1.0, "baseline_ready": true, "sufficient": true }
}
```

`status` is the deviation banded for display: `typical` (under 1 sigma),
`watch` (1–2), `concern` (2+), `better_than_baseline` (−1 or below),
`baseline_pending` (no baseline yet).

**`as_of` is the latest day with data, not today.** A watch syncs when it
feels like it, so this is usually last night. Clients should display it
rather than implying the numbers are current. It is capped at today, so a
device with a fast clock cannot make the app show a future night.

`missing_metrics` names what did not report, so the app can say "no SpO2
last night" instead of silently dropping a tile the user expects.

### `GET /wearable/body-response`

```json
{
  "user_id": "demo-user",
  "as_of": "2026-10-15",
  "device": { "device_id": "galaxy-watch-7-demo", "last_sync_at": "2026-10-15T07:30:00Z" },
  "body_response": { "score": 75, "level": "high" },
  "weighted_deviation": 2.25,
  "contributors": [
    { "metric": "nocturnal_cough_count", "display_name": "Nocturnal cough count", "contribution": 0.39 },
    { "metric": "resting_heart_rate", "display_name": "Resting heart rate", "contribution": 0.24 },
    { "metric": "hrv_rmssd", "display_name": "Heart rate variability (RMSSD)", "contribution": 0.11 }
  ],
  "drift": [
    { "metric": "resting_heart_rate", "display_name": "Resting heart rate",
      "consecutive_days": 4, "deviation": 2.67, "direction": "higher" },
    { "metric": "nocturnal_cough_count", "display_name": "Nocturnal cough count",
      "consecutive_days": 3, "deviation": 4.38, "direction": "higher" }
  ],
  "coverage": { "metrics_reported": 9, "metrics_scored": 9, "metrics_expected": 9,
                "weight_available": 1.0, "baseline_ready": true, "sufficient": true }
}
```

`contributors` ranks metrics by `w_i x d_i` as shares of the total rise, up
to 3. Only metrics on the *worse* side count — one that is better than
normal is not "behind" an elevated score. Same rule the dashboard applies
to environmental contributors.

### `GET /wearable/trends/{metric}?days=14`

`days` is 1–90, default 14, ending at the latest day with data (so the
chart does not trail off into empty days after a gap in syncing). Days with
no value are omitted rather than sent as nulls, so a chart can draw a real
gap. `404` for a metric not in the catalog.

### Errors

| Situation | Status |
|---|---|
| Malformed body, empty `samples`, bad date or non-numeric value | `422` (FastAPI validation) |
| `days` outside 1–90 | `422` |
| Unknown metric on `/trends/{metric}` | `404` |
| Individual samples failing validation | `200`, listed in `rejected` |

All instants are UTC ISO 8601 with a `Z`. All dates are plain ISO dates,
because every metric is a daily summary.

---

## There is no `user_id` parameter

The user comes from `get_current_user_id()` in
[`app/api/wearable.py`](app/api/wearable.py), which returns `demo-user`
until authentication exists. Adding auth changes only that function.

The `/dashboard` endpoint made the same choice; here the reason is sharper.
These are a named person's heart rate, sleep and overnight cough
recordings. A `user_id` query parameter would let any caller read — or on
the write endpoint, forge — another patient's health history. The user id
comes from the credential or not at all.

---

## How this plugs into the risk model

The write-up describes three layers. This service is the middle one:

| Layer | Source | Tells us |
|---|---|---|
| Exposure | Environmental model + forecast | What the user is breathing, now and ahead |
| **Body response** | **This service** | **Whether their physiology is drifting from baseline** |
| Airway symptoms | Nocturnal cough (ingested here as a metric) | Whether the airways are becoming irritated |

`GET /dashboard` currently computes `y = g + w_u · z` from exposure alone.
The natural next step is for `dashboard_service` to call
`GET /wearable/body-response` and use it two ways:

1. **As a risk term.** The body-response score is a second input alongside
   the environmental prediction — a user already drifting is more
   vulnerable to the same exposure.
2. **As the source of `w_u`.** The sensitivity weights the dashboard reads
   from `mock_user_personalization.json` are exactly what you learn by
   correlating a user's body-response history against their exposure
   history. This service accumulates the labels needed to fit them.

Nothing in the dashboard was changed for this deliverable; the two are
still independent sub-apps. Consume `coverage` alongside the score if you
do wire them together — a score with `sufficient: false` should carry less
weight.

---

## Demo data

[`app/services/demo_history.py`](app/services/demo_history.py) generates 35
days of history for `demo-user`. It is code rather than a JSON fixture for
two reasons: 35 days x 9 metrics is 300-odd unreviewable numbers, and dated
fixtures go stale — this one is anchored to the current date, so a demo
next month still shows "last night".

The shape is deliberate. Four weeks of ordinary days establish a baseline,
then the last three ramp into a textbook pre-exacerbation pattern: resting
heart rate and respiratory rate climbing, HRV and sleep quality falling,
nocturnal cough rising sharpest of all. The demo user scores **75 (high)**
with cough the top contributor and drift on three metrics — which is the
system detecting exactly what it was built to detect. Values are
deterministic, so tests and demos agree.

Two days of missing skin temperature are included on purpose, to exercise
the gap handling that real syncs will hit constantly.

---

## Storage

A JSON file at `app/data/wearable_store.json`, read once into memory and
rewritten on each accepted sync. Right for this stage: nothing to install,
the read path is dictionary lookups, and a teammate can open the file and
see exactly what the watch sent. Written via a temporary file and renamed
over the original, so an interrupted write leaves the previous file intact
rather than a half-written one; all access is under one lock, because
uvicorn runs sync endpoints in a thread pool and two syncs can land at
once.

Set `WEARABLE_STORE_PATH` to move it, or to `:memory:` to disable
persistence. Delete the file to reset and re-seed the demo user. It is
gitignored.

**The real home for this data is Postgres.** `backend/db/` already has the
migrations, session handling and repository pattern, and the natural schema
is one `wearable_observations` table keyed by `(user_id, variable_id,
observation_date)` — the same shape as `grid_observations`, with the same
unique constraint making re-syncs idempotent. Everything outside
`sample_store.py` works through its four functions, so that swap does not
reach the services.

---

## Folder layout

```text
backend/wearable/
├── app/
│   ├── main.py                        # FastAPI app; registers the routes
│   ├── api/wearable.py                # the five endpoints, input validation, user dependency
│   ├── models/wearable_models.py      # request and response schemas (Pydantic)
│   └── services/
│       ├── metrics_catalog.py         # the nine metrics and their properties
│       ├── baseline_service.py        # personal baselines, deviations, drift
│       ├── body_response_service.py   # the score, contributors, coverage
│       ├── ingest_service.py          # sample validation
│       ├── sample_store.py            # storage (JSON file; swap for Postgres)
│       ├── wearable_service.py        # joins the store to the analysis
│       └── demo_history.py            # DEMO DATA generator
├── tests/
│   ├── conftest.py                    # in-memory store fixture, history builders
│   ├── test_baseline_service.py       # baselines, deviations, drift
│   ├── test_body_response_service.py  # scoring, contributors, coverage
│   ├── test_ingest_service.py         # validation, idempotency, persistence
│   ├── test_demo_history.py           # the demo fixture's properties
│   └── test_wearable_api.py           # the endpoints through FastAPI's test client
├── pytest.ini
└── requirements.txt
```

`baseline_service` and `body_response_service` are pure functions over a
`{metric: {date: value}}` dictionary — they know nothing about users or
storage, which is what lets the tests check them against a hand-written
30-day series. `wearable_service` is the only module that fetches.

---

## Known limitations

- **No real device data.** Nothing is paired; the demo history is generated. Wiring a Galaxy Watch means a client app calling `POST /wearable/samples` — the Samsung Health Sensor SDK gives raw PPG, accelerometer, SpO2 and skin temperature.
- **No cough detector.** Cough counts are ingested like any other metric, but the HeAR-based classifier that produces them is not built. Nothing here assumes how they are computed.
- **Weights and the score scale are unfitted.** Priors from the literature, not values learned from labelled exacerbations. The 0–100 mapping and the level bands are placeholder constants.
- **One demo user** until authentication exists. The store is keyed by user id throughout, so multi-user works the moment real ids arrive.
- **No validation against clinical outcomes.** The score is a well-defined measure of departure from personal baseline. Whether a given score predicts an attack is an open question this deliverable does not answer — AAMOS-00 is the dataset to answer it with.
- **Daily summaries only.** No intraday series, so a sudden afternoon event is invisible until the next day's numbers. The 1–5 day buildup window is what this targets.
- **Not a medical device.** Nothing here should drive a treatment decision.
