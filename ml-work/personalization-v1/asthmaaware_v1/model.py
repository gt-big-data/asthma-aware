"""AsthmaAware personalization model — v1.

Pipeline
--------
1. exposure(X)                hourly concentrations  -> exposure metrics E (same shape)
2. ReferenceStats.fit(E)      Jan–Jun 2023 stats: per-pollutant mean/std, clean-air level,
                              and the distribution of log(RR) used for 0–100 ranking
3. general_risk(E, ref)       log(RR), RR, 0–100 score and per-pollutant contributions
4. PersonalModel              5 numbers per user, updated after every daily log;
                              predicts a personal severity map + uncertainty + top factors

Shapes (T = hours, C = 4 pollutants, H = 25, W = 27):
    X, E, Z            [T, C, H, W]
    log_rr, score      [T, H, W]
    contributions      [T, C, H, W]

Channel order everywhere: O3 (ppb), NO2 (ppb), SO2 (ppb), PM2.5 (µg/m³).
The coefficients assume GROUND-LEVEL concentrations in those units.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLLUTANTS = ("O3", "NO2", "SO2", "PM25")
UNITS = ("ppb", "ppb", "ppb", "µg/m³")

# Strickland et al. 2011 (Atlanta pediatric asthma ED visits): rate ratio per increment.
# beta = ln(RR) / increment  -> log rate ratio per unit concentration.
RATE_RATIOS = {"O3": (1.070, 25.0), "NO2": (1.105, 20.0), "SO2": (1.053, 20.0), "PM25": (1.044, 10.0)}
BETA = np.array([np.log(rr) / inc for rr, inc in (RATE_RATIOS[p] for p in POLLUTANTS)])

# Atlanta bounding box at 0.01°: 25 rows (lat) x 27 cols (lon)
BBOX = {"west": -84.55, "south": 33.39, "east": -84.28, "north": 33.64}
H, W = 25, 27
LATS = BBOX["south"] + 0.005 + 0.01 * np.arange(H)   # cell centres, south -> north
LONS = BBOX["west"] + 0.005 + 0.01 * np.arange(W)    # cell centres, west -> east

# Hours of history needed before the first valid exposure value:
#   O3 8-h mean (7) + trailing 24-h max (23) = 30, then lag-2 day (48) -> 78
HISTORY_HOURS = 78


# ---------------------------------------------------------------------------
# Step 1: exposure metrics
# ---------------------------------------------------------------------------

def _trailing_mean(x: np.ndarray, n: int) -> np.ndarray:
    """Mean over the last n hours (inclusive) along axis 0; first n-1 rows are NaN."""
    x = x.astype(np.float64)
    c = np.cumsum(x, axis=0)
    out = np.full_like(x, np.nan)
    prev = np.concatenate([np.zeros((1,) + x.shape[1:]), c[:-n]], axis=0)
    out[n - 1:] = (c[n - 1:] - prev) / n
    return out


def _trailing_max(x: np.ndarray, n: int) -> np.ndarray:
    """Max over the last n hours (inclusive) along axis 0; NaN propagates."""
    out = np.full(x.shape, np.nan)
    out[n - 1:] = sliding_window_view(x, n, axis=0).max(axis=-1)
    return out


def daily_metrics(X: np.ndarray) -> np.ndarray:
    """At every hour, the daily statistic each coefficient was estimated on,
    computed over the trailing 24 hours:

        O3     highest 8-hour mean
        NO2    highest 1-hour value
        SO2    highest 1-hour value
        PM2.5  24-hour mean
    """
    _check_cube(X)
    M = np.empty(X.shape, dtype=np.float64)
    M[:, 0] = _trailing_max(_trailing_mean(X[:, 0], 8), 24)
    M[:, 1] = _trailing_max(X[:, 1].astype(np.float64), 24)
    M[:, 2] = _trailing_max(X[:, 2].astype(np.float64), 24)
    M[:, 3] = _trailing_mean(X[:, 3], 24)
    return M


def exposure(X: np.ndarray) -> np.ndarray:
    """Exposure E = 3-day moving average (lags 0, 1, 2 days) of the daily metrics.

    Same shape as X. The first HISTORY_HOURS rows are NaN (not enough history),
    so feed in at least 78 hours of history before the forecast window.
    """
    M = daily_metrics(X)
    E = np.full_like(M, np.nan)
    E[48:] = (M[48:] + M[24:-24] + M[:-48]) / 3.0
    E[:HISTORY_HOURS] = np.nan   # blank all channels until every pollutant has full history
    return E


# ---------------------------------------------------------------------------
# Step 2: reference statistics (from the Jan–Jun 2023 data)
# ---------------------------------------------------------------------------

@dataclass
class ReferenceStats:
    mean: np.ndarray        # [C]  per-pollutant mean of E   (for standardizing)
    std: np.ndarray         # [C]  per-pollutant std of E
    clean: np.ndarray       # [C]  clean-air level = low percentile of E
    eta_sorted: np.ndarray  # sorted sample of log(RR) values, for 0–100 ranking

    @classmethod
    def fit(cls, E: np.ndarray, clean_pct: float = 5.0, max_samples: int = 200_000,
            seed: int = 0) -> "ReferenceStats":
        flat = np.moveaxis(E, 1, -1).reshape(-1, E.shape[1])
        flat = flat[~np.isnan(flat).any(axis=1)]
        mean, std = flat.mean(0), flat.std(0)
        clean = np.percentile(flat, clean_pct, axis=0)
        eta = (flat - clean) @ BETA
        if len(eta) > max_samples:
            eta = np.random.default_rng(seed).choice(eta, max_samples, replace=False)
        return cls(mean, std, clean, np.sort(eta))

    def standardize(self, E: np.ndarray) -> np.ndarray:
        """Z = (E - mean) / std with ONE mean/std per pollutant (never per pixel,
        which would erase the spatial contrast the heatmap is supposed to show)."""
        return (E - self.mean[None, :, None, None]) / self.std[None, :, None, None]

    def percentile(self, log_rr: np.ndarray) -> np.ndarray:
        """0–100 rank of each log(RR) value against the reference distribution."""
        pct = 100.0 * np.searchsorted(self.eta_sorted, log_rr) / len(self.eta_sorted)
        return np.where(np.isnan(log_rr), np.nan, pct)

    def save(self, path: str) -> None:
        np.savez(path, mean=self.mean, std=self.std, clean=self.clean, eta_sorted=self.eta_sorted)

    @classmethod
    def load(cls, path: str) -> "ReferenceStats":
        d = np.load(path)
        return cls(d["mean"], d["std"], d["clean"], d["eta_sorted"])


# ---------------------------------------------------------------------------
# Step 3: general (population) risk map
# ---------------------------------------------------------------------------

@dataclass
class GeneralRisk:
    log_rr: np.ndarray         # [T, H, W]  log relative risk vs a clean-air day
    contributions: np.ndarray  # [T, C, H, W]  each pollutant's share of log_rr
    score: np.ndarray          # [T, H, W]  0–100 percentile vs Jan–Jun 2023

    @property
    def rr(self) -> np.ndarray:
        """Relative risk, e.g. 1.2 = 20% more asthma ED visits than a clean-air day."""
        return np.exp(self.log_rr)


def general_risk(E: np.ndarray, ref: ReferenceStats) -> GeneralRisk:
    """log(RR) = Σ_p beta_p * (E_p - clean_p), for every hour and cell."""
    contrib = BETA[None, :, None, None] * (E - ref.clean[None, :, None, None])
    log_rr = contrib.sum(axis=1)
    return GeneralRisk(log_rr, contrib, ref.percentile(log_rr))


# ---------------------------------------------------------------------------
# Step 4: personal model
# ---------------------------------------------------------------------------

@dataclass
class PersonalModel:
    """Per-user model:  severity = b + k * log(RR) + w · z

    theta = [b, w_O3, w_NO2, w_SO2, w_PM25]  (5 numbers)

    Learned with Bayesian ridge regression. Two running totals are stored:
        P  [5,5]  precision (how confident we are, and in which directions)
        h  [5]    accumulated evidence
    and theta = P^-1 h. Each daily log is a rank-1 update, and the result is
    identical to refitting ridge on all of the user's logs from scratch.

    Hyperparameters (team decisions, defaults are placeholders):
        k                  severity points per unit log(RR), shared by all users
        sigma2             variance of day-to-day severity noise we can't explain
        prior_baseline     typical user's baseline severity on a clean day
        prior_sd_baseline  how far a user's baseline can plausibly be from that
        prior_sd_w         how big personal sensitivities plausibly are (in
                           severity points per 1 std of a pollutant)
    """
    k: float = 3.0
    sigma2: float = 0.35
    prior_baseline: float = 1.0
    prior_sd_baseline: float = 1.0
    prior_sd_w: float = 0.5
    P: np.ndarray = field(default=None, repr=False)
    h: np.ndarray = field(default=None, repr=False)
    n_logs: int = 0

    def __post_init__(self):
        if self.P is None:
            mu0 = np.array([self.prior_baseline, 0.0, 0.0, 0.0, 0.0])
            self.P = np.diag([self.prior_sd_baseline ** -2] + [self.prior_sd_w ** -2] * 4)
            self.h = self.P @ mu0
        self.P = np.asarray(self.P, float)
        self.h = np.asarray(self.h, float)

    # ---- parameters -------------------------------------------------------
    @property
    def theta(self) -> np.ndarray:
        return np.linalg.solve(self.P, self.h)

    @property
    def baseline(self) -> float:
        return float(self.theta[0])

    @property
    def w(self) -> np.ndarray:
        return self.theta[1:]

    @property
    def cov(self) -> np.ndarray:
        return np.linalg.inv(self.P)

    @property
    def w_sd(self) -> np.ndarray:
        """Posterior standard deviation of each sensitivity weight."""
        return np.sqrt(np.diag(self.cov)[1:])

    def model_sd(self, z_bar: np.ndarray) -> float:
        """Uncertainty of the personal part of a prediction at exposure z_bar
        (severity points). Shrinks as the user logs more; excludes daily noise."""
        phi = np.concatenate([[1.0], np.asarray(z_bar, float)])
        return float(np.sqrt(phi @ self.cov @ phi))

    def still_learning(self, recent_z: np.ndarray, threshold: float = 0.25) -> bool:
        """True -> show 'still learning your triggers'.

        recent_z: [n_days, 4] the user's recent daily exposures. We check prediction
        uncertainty on days like theirs rather than each weight separately: a pollutant
        that barely changes where the user lives can't be learned, but it also barely
        affects their predictions, so it shouldn't keep the banner up forever.
        """
        recent_z = np.atleast_2d(recent_z)
        return bool(max(self.model_sd(z) for z in recent_z) > threshold)

    # ---- learning ---------------------------------------------------------
    def update(self, z_bar: np.ndarray, log_rr_bar: float, severity: float) -> None:
        """Fold in one daily log.

        z_bar       [4]  the day's average standardized exposure where the user was
        log_rr_bar  the day's average general log(RR) there
        severity    what the user logged (0–4)
        """
        phi = np.concatenate([[1.0], np.asarray(z_bar, float)])
        r = severity - self.k * log_rr_bar          # only learn what the general map didn't explain
        self.P = self.P + np.outer(phi, phi) / self.sigma2
        self.h = self.h + phi * r / self.sigma2
        self.n_logs += 1

    # ---- prediction -------------------------------------------------------
    def predict(self, Z: np.ndarray, log_rr: np.ndarray) -> dict[str, np.ndarray]:
        """Personal map for every hour and cell.

        Returns severity [T,H,W] (0–4 scale, unclipped), score [T,H,W] (0–100),
        sd [T,H,W] (predictive standard deviation, severity points).
        """
        th = self.theta
        sev = th[0] + self.k * log_rr + np.einsum("tchw,c->thw", Z, th[1:])
        phi = np.concatenate([np.ones(Z[:, :1].shape), Z], axis=1)       # [T,5,H,W]
        var = np.einsum("tihw,ij,tjhw->thw", phi, self.cov, phi) + self.sigma2
        return {"severity": sev, "score": np.clip(25.0 * sev, 0, 100), "sd": np.sqrt(var)}

    def contributions(self, Z: np.ndarray, E: np.ndarray, ref: ReferenceStats) -> np.ndarray:
        """Each pollutant's push on severity, [T, C, H, W]. Exact, because the model is linear:
            contribution_p = k * beta_p * (E_p - clean_p)  +  w_p * z_p
                             (general effect)                (personal extra)
        severity = baseline + contributions.sum(axis=1)
        """
        general = self.k * BETA[None, :, None, None] * (E - ref.clean[None, :, None, None])
        personal = self.w[None, :, None, None] * Z
        return general + personal

    def top_factors(self, Z, E, ref, t: int, cell: tuple[int, int], n: int = 3) -> list[tuple[str, float]]:
        """Top-n pollutants pushing this user's severity up at hour t, cell (row, col)."""
        i, j = cell
        c = self.contributions(Z[t:t + 1, :, i:i + 1, j:j + 1], E[t:t + 1, :, i:i + 1, j:j + 1], ref)[0, :, 0, 0]
        order = np.argsort(-c)
        return [(POLLUTANTS[o], float(c[o])) for o in order[:n]]

    # ---- storage ----------------------------------------------------------
    def to_dict(self) -> dict:
        return {"k": self.k, "sigma2": self.sigma2, "prior_baseline": self.prior_baseline,
                "prior_sd_baseline": self.prior_sd_baseline, "prior_sd_w": self.prior_sd_w,
                "P": self.P.tolist(), "h": self.h.tolist(), "n_logs": self.n_logs}

    @classmethod
    def from_dict(cls, d: dict) -> "PersonalModel":
        return cls(**{**d, "P": np.array(d["P"]), "h": np.array(d["h"])})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _check_cube(X: np.ndarray) -> None:
    if X.ndim != 4 or X.shape[1] != 4:
        raise ValueError(f"expected [T, 4, H, W] with channels {POLLUTANTS}, got {X.shape}")
    if X.shape[0] <= HISTORY_HOURS:
        raise ValueError(f"need more than {HISTORY_HOURS} hours (history for the exposure lags), got {X.shape[0]}")
