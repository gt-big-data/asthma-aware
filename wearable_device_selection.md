# Choosing the Wearable: Device and API Selection

## Overview

[`asthma_wearable_cough_writeup.md`](asthma_wearable_cough_writeup.md) argues *why*
the system needs a smartwatch and nocturnal cough monitoring. This document
decides *which* device, and — more importantly — *which data pathway*.

The conclusion is a **Fitbit Air ($99) read through the Google Health API**,
with the Samsung Galaxy Watch and its Health Sensor SDK as the upgrade path.

The reasoning is not primarily about sensor quality. Almost every current
wearable measures the signals the literature points to. The binding
constraints turn out to be **whether you can get the data out at all**, and
**whether the device survives the night without being charged**.

---

## The selection criterion is data egress, not sensors

A wearable is only useful to this project if its daily per-user metrics can
reach our own backend. That is a much narrower filter than the spec sheets
suggest, and two of the obvious candidates fail it outright as of October 2026.

### Garmin is unavailable

Garmin would otherwise be the research default: long battery life, strong
metric coverage, a server-push Health API, and wide use in published studies.

**Garmin has paused new applications to the Connect Developer Program** and
removed the application form, with no announced reopening date. The program
also requires applicants to be a legal entity and rejects personal-use
applications. There is currently no route to access.

### The Fitbit Web API is being switched off

Support for the legacy Fitbit Web API **ended 30 September 2026**, and the API
**stops functioning on 30 October 2026**. Nearly every tutorial, blog post and
GitHub example for Fitbit data uses this API. Anything built on it now has days
of life left.

Its replacement is the **Google Health API** at `health.googleapis.com/v4/`,
which is live. Google OAuth 2.0 replaces Fitbit's own authorization, and
existing Fitbit OAuth tokens do not carry over — every user must re-consent.

### Apple is a poor fit for this project

Good sensors, and SpO2 returned to US models in August 2025 via a software
workaround that moves the calculation to the paired iPhone (though Masimo has
since filed another suit, so it is legally unsettled). The problems are
structural rather than technical:

- **No server-side API.** HealthKit data is readable only by an iOS app you write, so there is no path to our backend without building and distributing an iPhone app.
- **A Mac, Xcode, Swift and a $99/year developer account** are prerequisites.
- **iPhone users only**, which constrains recruitment.
- **About one day of battery**, which is disqualifying (see below).

---

## Battery life is the hidden decisive criterion

Our design is built almost entirely on **overnight** metrics: nighttime
respiratory rate, sleep efficiency and duration, overnight resting heart rate,
overnight SpO2, and nocturnal cough. A device that users charge at bedtime
produces no data during precisely the window the model depends on.

This quietly eliminates the flagship smartwatches:

| Device | Battery |
|---|---|
| Fitbit Inspire 3 | ~10 days |
| Fitbit Air / Charge 6 / Edge | ~7 days |
| Fitbit Sense 2 / Versa 4 | ~6 days |
| Google Pixel Watch 4 | 30–40 hours |
| Apple Watch | ~1 day |

A 7-to-10 day tracker gets charged in a spare daytime moment. A 30-hour watch
gets charged overnight, because that is when people are not looking at it.

---

## Device options

### Pathway A — Google Health API (no mobile app required)

Server-to-server REST with Google OAuth. A scheduled job can pull daily
summaries and POST them to our ingest endpoint; **no iOS or Android app is
needed**. Sources are Fitbit devices and the Pixel Watch, and every data point
carries provenance identifying the device that produced it.

| Model | Price | Battery | Covers all 9 metrics | Notes |
|---|---|---|---|---|
| **Fitbit Air** ⭐ | **$99** | ~7 days | Yes | Screenless, 12 g. HR/HRV, SpO2, skin temp, respiratory rate, sleep stages, 3-axis IMU, AFib alerts |
| **Fitbit Inspire 3** | ~$80 (list $99) | ~10 days | Yes | Longest battery, lowest price. 2022 model, so shortest remaining support life |
| **Fitbit Charge 6** | $160 | ~7 days | Yes | Just superseded by Edge; expect discounting |
| **Fitbit Edge** | $179 | ~7 days (2 with always-on) | Yes | Announced 12 Oct 2026, ships 27 Oct. GPS, ECG, **user-replaceable battery** |
| **Fitbit Sense 2** | ~$250 | ~6 days | Yes | Most complete sensor array; adds EDA |
| **Fitbit Versa 4** | ~$200 | ~6 days | Yes | Smartwatch form factor |
| **Google Pixel Watch 4** | $349–399 | 30–40 h | Yes | Full Wear OS; battery conflicts with overnight collection |

HRV, breathing rate, SpO2, skin temperature, resting heart rate and sleep
stages are all available on Fitbit's **free Base tier** in 2026. No Premium
subscription is required.

### Pathway B — Samsung Health Sensor SDK (raw signal)

Supported on **Galaxy Watch 4 series and later** — so Watch 8, Watch 8 Classic
and Watch Ultra. SDK v1.4.1 exposes raw **accelerometer, PPG (green/red/IR),
heart rate including inter-beat intervals (IBI), EDA, ECG and skin
temperature**.

Two things only this pathway gives us:

1. **Raw IBI**, so we can compute RMSSD and other HRV features ourselves rather than accepting a vendor's single daily number. This is closer to what Rahman et al. (2019) did.
2. **Raw accelerometer**, which is the only way to implement the cough cross-check described in the write-up — confirming a detected cough came from the user rather than a partner or the TV. **No cloud aggregation API exposes motion data at the resolution this needs.**

The cost is engineering: a Wear OS app plus a companion phone app in Kotlin,
versus a scheduled job. Roughly 3–5× the work. It also avoids Google's OAuth
review entirely, since data flows through our own Android app under Android
permissions — which matters for the token problem below.

---

## Metric mapping

The Google Health API maps almost one-to-one onto the metric catalog already
implemented in `backend/wearable/app/services/metrics_catalog.py`:

| Catalog metric | Google Health API data type |
|---|---|
| `resting_heart_rate` | `daily-resting-heart-rate` |
| `hrv_rmssd` | `daily-heart-rate-variability` (RMSSD/SDNN) |
| `respiratory_rate` | `daily-respiratory-rate`, `respiratory-rate-sleep-summary` |
| `spo2` | `daily-oxygen-saturation` |
| `skin_temperature` | `daily-sleep-temperature-derivations` |
| `sleep_duration`, `sleep_efficiency` | `sleep` (session, with stages) |
| `steps` | `steps` |
| `nocturnal_cough_count` | — phone microphone, not the watch |

Eight of nine metrics arrive as daily summaries, which is exactly the
granularity the catalog was built around. The adapter is a translation layer,
not a redesign.

---

## Recommendation: Fitbit Air, $99

It covers all nine metrics, runs about a week per charge, and costs $99 — three
units for the price of two Charge 6s.

The non-obvious argument is that **being screenless is a feature here, not a
compromise**. The write-up's central premise is passive collection "without
extra effort on their part," and that adherence to active monitoring collapses
once people feel well. A screenless 12-gram tracker removes the interaction
surface entirely: nothing to check, nothing to engage with, and light enough
that people keep it on overnight. Overnight compliance is what the whole model
rests on, and the Air optimises for it more directly than any watch does.

**Alternatives, if the Air does not fit:**

- **Budget / more units:** Inspire 3 at ~$80 with ~10-day battery. Accept the shorter support horizon.
- **Longest service life:** Edge at $179. A user-replaceable battery is unusual at this size and matters over a multi-month deployment.
- **Raw signal, >100 participants, or the cough cross-check:** Galaxy Watch 8 and Pathway B.

Buy **one** unit and get it end-to-end into Postgres before ordering more.

---

## Open risks

### 1. The OAuth token lifetime problem (highest priority)

All `googlehealth.*` scopes are classified **Restricted**. This creates a fork:

- **Testing mode** allows up to **100 users** with no approval and no cost — ideal for a pilot. But for an External consent screen in Testing status, **refresh tokens expire after 7 days**. For daily longitudinal collection that means participants re-consenting weekly, which will quietly starve the dataset.
- **Publishing to production** removes the 7-day expiry, but for restricted scopes it triggers OAuth verification including a third-party security review, with **no published SLA**. Past 100 users an annual CASA security assessment is also reported to apply.

**Action: resolve this in week one, before buying devices in quantity.** Create
the Cloud project, request the scopes, and find out which path a university
research project actually gets. This is the item most likely to sink the
project in month three if it is discovered late. Pathway B sidesteps it
entirely.

### 2. Skin temperature is reported as *variation*, not absolute temperature

Fitbit reports skin temperature as a deviation from the user's own baseline,
and the API type is named `daily-sleep-temperature-derivations`. The
`skin_temperature` metric as currently implemented declares
`valid_min=30.0, valid_max=42.0` in degrees Celsius and would **reject every
incoming sample as out of plausible range**.

Fix before the first real sync: either redefine the metric as baseline-relative
(roughly −5 to +5, keeping `HIGHER_IS_WORSE`) or convert on ingest. It carries
the lowest weight in the catalog (0.03), so nothing else is blocked by it, but
100% rejection on day one is a confusing way to start.

### 3. Fitbit is being absorbed into Google

The Fitbit app is becoming "Google Health" and the hardware line is being
folded into Pixel. The Google Health API is the strategic direction rather than
a stopgap, so near-term risk is low — but the Edge replaced the Charge line
under a new name, and model continuity should not be assumed.

### 4. Air and Edge are very new

The Air shipped around May 2026 and the Edge ships 27 October 2026. Confirm the
Google Health API serves all required data types for the specific model
purchased before committing to a bulk order.

---

## Next steps

1. Create the Google Cloud project, enable the Health API, and determine the verification path for restricted scopes (risk 1).
2. Buy one Fitbit Air and pair it.
3. Fix the `skin_temperature` metric definition (risk 2).
4. Build the Google Health API adapter: pull daily summaries, translate to the catalog, POST to `/wearable/samples`.
5. Confirm a week of real data lands in Postgres with sensible baselines before scaling up.

---

## Sources

- Fitbit Web API deprecation and Google Health migration: <https://www.themomentum.ai/blog/fitbit-web-api-deprecation-google-health-migration>
- Google Health API: <https://developers.google.com/health>
- Google Health API developer checklist: <https://developers.google.com/health/developer-checklist>
- Google Health API data types: <https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints>
- Garmin Connect Developer Program pause: <https://tryterra.co/blog/garmin-connect-developer-program-pause>
- Google OAuth testing-mode 7-day refresh token expiry: <https://dev.to/ko-hi/googles-oauth-testing-mode-expires-refresh-tokens-in-7-days-publish-the-consent-screen-before-24hm>
- Fitbit Air: <https://www.tomsguide.com/wellness/fitness-trackers/fitbit-air-review>, <https://ennap.com/blogs/articles/google-fitbit-air-specs>
- Fitbit Edge: <https://blog.google/products-and-platforms/devices/fitbit/google-fitbit-edge/>, <https://the5krunner.com/2026/10/12/fitbit-edge-all-you-need-to-know/>
- Fitbit Inspire 3: <https://www.dcrainmaker.com/2022/10/fitbit-inspire-review.html>
- Fitbit product line: <https://en.wikipedia.org/wiki/List_of_Fitbit_products>
- Fitbit free-tier health metrics: <https://community.fitbit.com/t5/The-Pulse-Fitbit-Community-Blog/What-should-I-know-about-health-metrics-in-the-Fitbit-app/ba-p/5542412>
- Samsung Health Sensor SDK: <https://developer.samsung.com/health/sensor/overview.html>
- Apple Watch SpO2 restoration: <https://www.macrumors.com/2025/08/14/blood-oxygen-monitoring-returning-to-apple-watch/>
