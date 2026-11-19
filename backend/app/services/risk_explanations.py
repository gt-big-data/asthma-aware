"""Independent, one-pollutant reference comparisons of the actual score pipeline.

Percentile ranks, confidence blending and clipping are nonlinear: marginal score
differences are not additive contributions or causal health-effect estimates.
Expose the model exposure metric, never label it an instantaneous concentration.
"""
import numpy as np

from app.services.geos_dashboard import RiskDriver
from app.services.personalization_model import POLLUTANTS, UNITS, personal_scores


def explain_hours(E, ref, model, scores):
    """E is the selected single-location [T,4,1,1] exposure cube."""
    alternatives = []
    for channel in range(4):
        comparison = E.copy()
        comparison[:, channel] = ref.clean[channel]
        alternatives.append(personal_scores(comparison, ref, model)["score"][:, 0, 0])
    result = []
    for t, score in enumerate(scores):
        if not np.isfinite(score) or not np.isfinite(E[t]).all():
            result.append([])
            continue
        drivers = [RiskDriver(
            pollutant=pollutant, exposure=float(E[t, channel, 0, 0]),
            reference=float(ref.clean[channel]), unit=UNITS[channel],
            impact=round(float(score - alternatives[channel][t]), 1),
            score_without=round(float(alternatives[channel][t])),
        ) for channel, pollutant in enumerate(POLLUTANTS)]
        result.append(sorted(drivers, key=lambda driver: driver.impact, reverse=True))
    return result
