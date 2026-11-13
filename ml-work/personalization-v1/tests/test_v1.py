import numpy as np
import pytest

from asthmaaware_v1 import (BETA, HISTORY_HOURS, PersonalModel, ReferenceStats, exposure,
                            general_risk)
from asthmaaware_v1.synthetic import (SimUser, daily_user_exposure, default_personas,
                                      simulate_logs, synthetic_grid)


@pytest.fixture(scope="module")
def pipeline():
    X, _ = synthetic_grid(days=120, seed=3)
    E = exposure(X)
    ref = ReferenceStats.fit(E)
    g = general_risk(E, ref)
    Z = ref.standardize(E)
    return X, E, ref, g, Z


def test_beta_matches_strickland():
    np.testing.assert_allclose(BETA, [0.00271, 0.00499, 0.00258, 0.00431], atol=5e-6)


def test_exposure_history_and_metrics():
    T = 200
    X = np.ones((T, 4, 2, 2))
    X[100, 1] = 50.0                      # one NO2 spike at hour 100
    E = exposure(X)
    assert np.isnan(E[:HISTORY_HOURS]).all()
    assert not np.isnan(E[HISTORY_HOURS:]).any()
    # NO2 metric is the 1-h max over trailing 24 h: spike enters every hour 100..123,
    # and the 3-day average sees it at lag 0 (100..123), lag 1 (124..147), lag 2 (148..171)
    assert np.isclose(E[110, 1, 0, 0], (50 + 1 + 1) / 3)
    assert np.isclose(E[172, 1, 0, 0], 1.0)
    # PM2.5 (24-h mean) of a constant field stays constant
    assert np.allclose(E[HISTORY_HOURS:, 3], 1.0)


def test_general_risk_is_sum_of_contributions(pipeline):
    _, E, ref, g, _ = pipeline
    np.testing.assert_allclose(np.nansum(g.contributions, 1), np.nan_to_num(g.log_rr))
    s = g.score[~np.isnan(g.score)]
    assert s.min() >= 0 and s.max() <= 100


def test_standardize_is_per_pollutant_not_per_pixel(pipeline):
    _, E, ref, _, Z = pipeline
    flat = np.moveaxis(Z, 1, -1).reshape(-1, 4)
    flat = flat[~np.isnan(flat).any(1)]
    np.testing.assert_allclose(flat.mean(0), 0, atol=1e-6)
    np.testing.assert_allclose(flat.std(0), 1, atol=1e-6)
    # spatial contrast survives: the time-mean of Z still differs across cells
    assert np.nanmean(Z, axis=0).std(axis=(1, 2)).min() > 0.1


def test_cold_start_equals_general_map(pipeline):
    _, _, _, g, Z = pipeline
    m = PersonalModel(k=3.0, prior_baseline=1.0)
    pred = m.predict(Z[200:224], g.log_rr[200:224])
    np.testing.assert_allclose(pred["severity"], 1.0 + 3.0 * g.log_rr[200:224])


def test_incremental_update_equals_batch_ridge():
    rng = np.random.default_rng(0)
    n = 60
    Zb, eta = rng.normal(size=(n, 4)), rng.uniform(0, 0.4, n)
    y = rng.integers(0, 5, n).astype(float)
    m = PersonalModel()
    P0, h0 = m.P.copy(), m.h.copy()
    for i in range(n):
        m.update(Zb[i], eta[i], y[i])
    Phi = np.column_stack([np.ones(n), Zb])
    r = y - m.k * eta
    theta_batch = np.linalg.solve(P0 + Phi.T @ Phi / m.sigma2, h0 + Phi.T @ r / m.sigma2)
    np.testing.assert_allclose(m.theta, theta_batch, atol=1e-10)


def test_parameter_recovery(pipeline):
    """Plant known sensitivities, simulate continuous logs, check the model finds them."""
    _, _, _, g, Z = pipeline
    rng = np.random.default_rng(7)
    user = SimUser("test", (8, 18), 1.2, np.array([0.8, 0.0, 0.0, 0.4]), noise_sd=0.4)
    _, zb, eb = daily_user_exposure(Z, g.log_rr, user.cell)
    y = simulate_logs(user, zb, eb, k=3.0, rng=rng, integer=False)
    m = PersonalModel(k=3.0, sigma2=0.16)
    for i in range(len(y)):
        m.update(zb[i], eb[i], y[i])
    # O3 and PM2.5 vary a lot at this cell, so they should be learned tightly
    assert abs(m.w[0] - 0.8) < 0.15
    assert abs(m.w[3] - 0.4) < 0.15
    assert abs(m.baseline - 1.2) < 0.2


def test_contributions_reconstruct_severity(pipeline):
    _, E, ref, g, Z = pipeline
    m = PersonalModel()
    m.update(np.array([1.0, -0.5, 0.2, 0.3]), 0.2, 3.0)
    sl = slice(300, 310)
    pred = m.predict(Z[sl], g.log_rr[sl])["severity"]
    contrib = m.contributions(Z[sl], E[sl], ref)
    np.testing.assert_allclose(m.baseline + contrib.sum(1), pred, atol=1e-9)


def test_state_roundtrip():
    m = PersonalModel()
    m.update(np.ones(4), 0.1, 2.0)
    m2 = PersonalModel.from_dict(m.to_dict())
    np.testing.assert_allclose(m.theta, m2.theta)
    assert m2.n_logs == 1


def test_rejects_bad_shapes():
    with pytest.raises(ValueError):
        exposure(np.zeros((100, 3, 25, 27)))
    with pytest.raises(ValueError):
        exposure(np.zeros((50, 4, 25, 27)))
