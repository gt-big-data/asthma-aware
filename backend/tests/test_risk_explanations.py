"""Explanations must follow the real percentile/blend/clipping pipeline."""
import numpy as np

from app.services.personalization_model import POLLUTANTS, PersonalModel, ReferenceStats, personal_scores
from app.services.risk_explanations import explain_hours
from tests.test_risk_map import published


def test_population_explanations_recompute_one_reference_at_a_time_without_mutation():
    E = np.array([[30., 10., 3., 8.], [0., 0., 0., 0.], [np.nan, 4., 2., 5.]])[:, :, None, None]
    original = E.copy()
    ref = ReferenceStats(np.ones(4), np.ones(4), np.array([5., 1., .5, 2.]), np.linspace(-.3, .3, 10000))
    model = PersonalModel()
    scores = personal_scores(E, ref, model)["score"][:, 0, 0]
    explanations = explain_hours(E, ref, model, scores)
    assert explanations[2] == []
    assert len(explanations[0]) == 4
    assert any(driver.impact < 0 for driver in explanations[1])
    for t in (0, 1):
        assert explanations[t] == sorted(explanations[t], key=lambda driver: driver.impact, reverse=True)
        for driver in explanations[t]:
            channel = POLLUTANTS.index(driver.pollutant)
            comparison = E.copy()
            comparison[:, channel] = ref.clean[channel]
            expected = personal_scores(comparison, ref, model)["score"][t, 0, 0]
            assert driver.score_without == round(float(expected))
            assert driver.impact == round(float(scores[t] - expected), 1)
            assert driver.exposure == E[t, channel, 0, 0]
            assert driver.reference == ref.clean[channel]
    np.testing.assert_allclose(E, original, equal_nan=True)


def test_personal_explanations_include_confidence_and_score_limits():
    E = np.array([[30., 10., 3., 8.], [60., 50., 40., 30.]])[:, :, None, None]
    ref = ReferenceStats(np.ones(4), np.ones(4), np.zeros(4), np.linspace(0, .4, 10000))
    P = np.eye(5) * 1e8
    model = PersonalModel(P=P, h=P @ np.array([4., -.1, .2, .1, -.05]), n_logs=25)
    actual = personal_scores(E, ref, model)["score"][:, 0, 0]
    explanations = explain_hours(E, ref, model, actual)
    for t, drivers in enumerate(explanations):
        for driver in drivers:
            changed = E.copy()
            changed[:, POLLUTANTS.index(driver.pollutant)] = 0
            expected = personal_scores(changed, ref, model)["score"][t, 0, 0]
            assert driver.impact == round(float(actual[t] - expected), 1)
            assert 0 <= driver.score_without <= 100
    assert actual[1] == 100


def test_public_hourly_api_includes_four_drivers_and_no_fabricated_missing_explanation(published):
    client, _, _, _, _, _ = published
    result = client.get("/v1/dashboard/risk?lat=33.75&lon=-84.39").json()
    for hour in result["hourly"]:
        assert len(hour["drivers"]) == 4
        assert {driver["pollutant"] for driver in hour["drivers"]} == set(POLLUTANTS)
        assert next(driver for driver in hour["drivers"] if driver["pollutant"] == "PM25")["unit"] == "µg/m³"
    missing = client.get("/v1/dashboard/risk?lat=31&lon=-82").json()
    assert all(hour["drivers"] == [] for hour in missing["hourly"])
