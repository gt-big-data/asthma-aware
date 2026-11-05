"""The metrics the watch and phone report, and what each one means.

One entry per metric. Everything else in this folder is driven by this
table, so adding a sensor is a matter of adding a row here (plus demo
values in ``demo_history.py``) rather than touching the services.

Each metric is a **daily summary**, one number per user per day -- the
granularity consumer watch SDKs actually expose (Samsung Health, Apple
HealthKit and Fitbit all surface nightly resting heart rate, HRV and
sleep rather than raw beat-to-beat series). It is also the granularity
the science is about: the FACET study of 425 severe exacerbations found
symptoms build over *days*, so day-over-day drift is the signal, not
minute-to-minute jitter.

Field notes
-----------
``adverse_direction``
    Which way is bad. ``+1`` means a higher value is worse (heart rate,
    cough count), ``-1`` means lower is worse (HRV, SpO2, sleep). Every
    deviation in this folder is sign-corrected with it, so a positive
    deviation always reads "worse for this user" no matter the metric.
    Without this, averaging a rising heart rate against a falling HRV
    would cancel two warning signs into zero.

``valid_min`` / ``valid_max``
    Physiologically plausible range. Consumer wearables do emit junk --
    a dropped PPG contact reports a heart rate of 0, a watch taken off
    reports a skin temperature near room temperature. Out-of-range
    samples are rejected at ingest rather than stored, because a single
    0 bpm night poisons the baseline median for the next four weeks.

``min_spread``
    Floor on the baseline's robust spread, in the metric's own unit.
    A user with an unusually steady metric can have a MAD of exactly
    zero over the baseline window, which would make every deviation
    infinite. The floor is set to roughly each sensor's own measurement
    noise, so "quieter than the sensor can resolve" is treated as
    sensor-noise-sized rather than as zero.

``weight``
    Share of the body-response score. Weights sum to 1.0 and are graded
    by how strongly the literature supports each signal: resting heart
    rate and cough are the two markers the 2024 ERJ Open Research review
    of digital markers of asthma exacerbations called most promising, so
    they carry the most; HRV (Rahman et al. 2019) and nocturnal
    respiratory rate follow; sleep, activity and skin temperature are
    supporting context. These are *priors*, not fitted values -- they
    are the first thing to replace with weights learned from AAMOS-00.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

HIGHER_IS_WORSE = 1
LOWER_IS_WORSE = -1

# Where a metric comes from, for the client to group by.
WATCH = "watch"
PHONE = "phone"


@dataclass(frozen=True)
class Metric:
    slug: str
    display_name: str
    unit: str
    adverse_direction: int
    valid_min: float
    valid_max: float
    min_spread: float
    weight: float
    source: str
    description: str

    @property
    def direction_label(self) -> str:
        """``adverse_direction`` as a word, for API responses."""
        return "higher" if self.adverse_direction == HIGHER_IS_WORSE else "lower"


METRICS: Tuple[Metric, ...] = (
    Metric(
        slug="resting_heart_rate",
        display_name="Resting heart rate",
        unit="bpm",
        adverse_direction=HIGHER_IS_WORSE,
        valid_min=30.0,
        valid_max=150.0,
        min_spread=1.5,
        weight=0.20,
        source=WATCH,
        description="Lowest sustained heart rate overnight. Drifts upward days before an exacerbation.",
    ),
    Metric(
        slug="nocturnal_cough_count",
        display_name="Nocturnal cough count",
        unit="coughs/night",
        adverse_direction=HIGHER_IS_WORSE,
        valid_min=0.0,
        valid_max=2000.0,
        min_spread=2.0,
        weight=0.20,
        source=PHONE,
        description="Coughs detected in the phone's overnight audio. Direct read on airway irritation.",
    ),
    Metric(
        slug="hrv_rmssd",
        display_name="Heart rate variability (RMSSD)",
        unit="ms",
        adverse_direction=LOWER_IS_WORSE,
        valid_min=1.0,
        valid_max=300.0,
        min_spread=3.0,
        weight=0.15,
        source=WATCH,
        description="Overnight beat-to-beat variability. Falls under physiological stress.",
    ),
    Metric(
        slug="respiratory_rate",
        display_name="Nighttime respiratory rate",
        unit="breaths/min",
        adverse_direction=HIGHER_IS_WORSE,
        valid_min=4.0,
        valid_max=40.0,
        min_spread=0.5,
        weight=0.15,
        source=WATCH,
        description="Breaths per minute during sleep. Rises with airway obstruction and infection.",
    ),
    Metric(
        slug="spo2",
        display_name="Blood oxygen saturation",
        unit="%",
        adverse_direction=LOWER_IS_WORSE,
        valid_min=70.0,
        valid_max=100.0,
        min_spread=0.5,
        weight=0.10,
        source=WATCH,
        description="Overnight average SpO2. A late signal, but a specific one when it moves.",
    ),
    Metric(
        slug="sleep_efficiency",
        display_name="Sleep efficiency",
        unit="%",
        adverse_direction=LOWER_IS_WORSE,
        valid_min=0.0,
        valid_max=100.0,
        min_spread=2.0,
        weight=0.08,
        source=WATCH,
        description="Share of time in bed actually asleep. Nocturnal symptoms fragment sleep.",
    ),
    Metric(
        slug="sleep_duration",
        display_name="Sleep duration",
        unit="hours",
        adverse_direction=LOWER_IS_WORSE,
        valid_min=0.0,
        valid_max=24.0,
        min_spread=0.5,
        weight=0.05,
        source=WATCH,
        description="Total time asleep overnight.",
    ),
    Metric(
        slug="steps",
        display_name="Daily steps",
        unit="steps",
        adverse_direction=LOWER_IS_WORSE,
        valid_min=0.0,
        valid_max=100000.0,
        min_spread=500.0,
        weight=0.04,
        source=WATCH,
        description="Activity level. Reduced exercise tolerance is a recognised sign of poor control.",
    ),
    Metric(
        slug="skin_temperature",
        display_name="Skin temperature",
        unit="degC",
        adverse_direction=HIGHER_IS_WORSE,
        valid_min=30.0,
        valid_max=42.0,
        min_spread=0.2,
        weight=0.03,
        source=WATCH,
        description="Overnight wrist temperature. Raised by the infections that often trigger attacks.",
    ),
)

BY_SLUG: Dict[str, Metric] = {metric.slug: metric for metric in METRICS}

TOTAL_WEIGHT = sum(metric.weight for metric in METRICS)


def get(slug: str) -> Metric:
    """The metric with this slug. Raises ``KeyError`` if unknown."""
    return BY_SLUG[slug]


def is_known(slug: str) -> bool:
    return slug in BY_SLUG


def slugs() -> List[str]:
    """Every metric slug, in catalog (descending weight) order."""
    return [metric.slug for metric in METRICS]


def in_valid_range(slug: str, value: float) -> bool:
    metric = BY_SLUG[slug]
    return metric.valid_min <= value <= metric.valid_max
