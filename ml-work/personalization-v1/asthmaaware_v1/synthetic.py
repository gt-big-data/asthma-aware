"""Synthetic hourly pollutant grids and simulated users for AsthmaAware v1.

Everything here is FAKE data, built only so the v1 pipeline can be developed and
demoed before the real upscaled forecast grid is ready. The spatial features
(highway corridors, urban core, an industrial point source) are invented and do
not correspond to real Atlanta geography.

Output shape matches what the real pipeline should hand over:
    X : [T, 4, 25, 27]  hourly surface concentrations
        channel order = (O3 ppb, NO2 ppb, SO2 ppb, PM2.5 µg/m³)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import H, W, POLLUTANTS


# ---------------------------------------------------------------------------
# Synthetic pollutant grid
# ---------------------------------------------------------------------------

def _spatial_fields(rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Static [H, W] maps that shape each pollutant across the grid."""
    ii, jj = np.meshgrid(np.arange(H), np.arange(W), indexing="ij")

    def line_dist(i0, j0, i1, j1):
        # distance (in cells) from each cell to the segment (i0,j0)-(i1,j1)
        p = np.stack([ii - i0, jj - j0], -1).astype(float)
        d = np.array([i1 - i0, j1 - j0], float)
        t = np.clip((p @ d) / (d @ d), 0, 1)
        return np.linalg.norm(p - t[..., None] * d, axis=-1)

    # two made-up highway corridors
    hwy = (np.exp(-line_dist(0, 6, 24, 15) ** 2 / (2 * 1.3 ** 2))
           + np.exp(-line_dist(14, 0, 9, 26) ** 2 / (2 * 1.3 ** 2)))
    hwy = hwy / hwy.max()
    # urban core near the north-centre of the box
    urban = np.exp(-((ii - 22) ** 2 + (jj - 13) ** 2) / (2 * 7.0 ** 2))
    # industrial SO2 source in the south-west
    so2_src = np.exp(-((ii - 5) ** 2 + (jj - 5) ** 2) / (2 * 2.5 ** 2))
    # gentle random texture so the maps aren't too clean
    texture = rng.normal(0, 1, (H, W))
    for _ in range(3):  # cheap smoothing
        texture = (texture + np.roll(texture, 1, 0) + np.roll(texture, -1, 0)
                   + np.roll(texture, 1, 1) + np.roll(texture, -1, 1)) / 5
    texture = texture / texture.std()

    return {
        "O3": 1.0 - 0.18 * urban - 0.08 * hwy + 0.03 * texture,   # NO titration lowers O3 downtown
        "NO2": 6.0 + 20.0 * hwy + 10.0 * urban + 0.8 * texture,
        "SO2": 1.0 + 9.0 * so2_src + 0.4 * urban + 0.1 * texture,
        "PM25": 8.0 + 3.0 * urban + 2.0 * hwy + 0.4 * texture,
    }


def _daily_weather(n_days: int, rng: np.random.Generator) -> np.ndarray:
    """[n_days, 4] multiplicative day-to-day weather factors.

    A shared 'stagnation' factor (bad-air days hit all pollutants) plus a
    pollutant-specific factor, both AR(1). The shared factor is kept moderate so
    the four pollutants aren't so correlated that user weights become unidentifiable.
    """
    def ar1(n, phi, sd):
        x = np.zeros(n)
        x[0] = rng.normal(0, sd)
        for t in range(1, n):
            x[t] = phi * x[t - 1] + rng.normal(0, sd * np.sqrt(1 - phi ** 2))
        return x

    shared = ar1(n_days, 0.7, 0.18)
    own = np.stack([ar1(n_days, 0.6, s) for s in (0.25, 0.25, 0.35, 0.22)], -1)
    return np.exp(shared[:, None] + own)


def synthetic_grid(days: int = 181, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Hourly synthetic pollution starting 2023-01-01.

    Returns
    -------
    X : float32 [days*24, 4, 25, 27]
    times : datetime64[h] [days*24]
    """
    rng = np.random.default_rng(seed)
    T = days * 24
    times = np.datetime64("2023-01-01T00", "h") + np.arange(T)
    hour = np.arange(T) % 24
    day = np.arange(T) // 24
    frac = day / max(days - 1, 1)  # 0 in early Jan -> 1 in late Jun

    sp = _spatial_fields(rng)

    # diurnal shapes
    h = hour.astype(float)
    d_o3 = 0.45 + 0.75 * np.exp(-(h - 15) ** 2 / (2 * 3.0 ** 2))
    d_no2 = 1.0 + 0.6 * np.exp(-(h - 8) ** 2 / 3) + 0.45 * np.exp(-(h - 18) ** 2 / 4) - 0.35 * np.exp(-(h - 14) ** 2 / 8)
    d_so2 = 1.0 + 0.5 * np.exp(-(h - 11) ** 2 / 6)
    d_pm = 1.0 + 0.15 * np.cos(2 * np.pi * (h - 7) / 24)

    # seasonal trends over Jan -> Jun
    s_o3 = 0.65 + 0.6 * frac          # ozone season ramps up into summer
    s_no2 = 1.2 - 0.35 * frac         # NO2 higher in winter (shallower mixing)
    s_so2 = 1.1 - 0.2 * frac
    s_pm = 0.95 + 0.15 * np.sin(np.pi * frac)

    # day-to-day weather, smoothly interpolated to hourly
    wx_daily = _daily_weather(days + 1, rng)
    t_frac = np.arange(T) / 24.0
    wx = np.stack([np.interp(t_frac, np.arange(days + 1), wx_daily[:, c]) for c in range(4)], -1)  # [T,4]

    X = np.empty((T, 4, H, W), dtype=np.float32)
    X[:, 0] = 42.0 * sp["O3"][None] * (d_o3 * s_o3 * wx[:, 0])[:, None, None]
    X[:, 1] = sp["NO2"][None] * (d_no2 * s_no2 * wx[:, 1])[:, None, None]
    X[:, 2] = sp["SO2"][None] * (d_so2 * s_so2 * wx[:, 2])[:, None, None]
    X[:, 3] = sp["PM25"][None] * (d_pm * s_pm * wx[:, 3])[:, None, None]

    # small cell-by-hour noise
    X *= np.exp(rng.normal(0, 0.05, X.shape)).astype(np.float32)
    np.clip(X, 0, None, out=X)
    return X, times


# ---------------------------------------------------------------------------
# Simulated users
# ---------------------------------------------------------------------------

@dataclass
class SimUser:
    """A fake user with KNOWN sensitivities, used to check the model can recover them."""
    name: str
    cell: tuple[int, int]          # (row, col) of where they spend the day
    baseline: float                # true b_u
    w: np.ndarray                  # true per-pollutant weights, order = POLLUTANTS
    noise_sd: float = 0.5          # day-to-day randomness in severity

    def describe(self) -> str:
        parts = [f"{p} {wv:+.2f}" for p, wv in zip(POLLUTANTS, self.w) if abs(wv) > 1e-9]
        return f"{self.name}: baseline {self.baseline:.1f}; " + (", ".join(parts) or "no extra sensitivities")


def default_personas() -> list[SimUser]:
    return [
        SimUser("Typical user", (12, 13), 1.2, np.array([0.0, 0.0, 0.0, 0.0])),
        SimUser("Ozone-sensitive", (6, 20), 1.3, np.array([0.8, 0.0, 0.0, 0.0])),
        SimUser("Traffic-sensitive", (14, 9), 1.0, np.array([0.0, 0.7, 0.0, 0.3])),
    ]


def daily_user_exposure(Z: np.ndarray, log_rr: np.ndarray, cell: tuple[int, int],
                        hours_per_day: int = 24) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Collapse hourly arrays to one row per day at the user's cell.

    Returns (day_index [D], z_bar [D, 4], eta_bar [D]); days with any missing hour
    (the warm-up history period) are dropped.
    """
    i, j = cell
    T = Z.shape[0] - Z.shape[0] % hours_per_day
    z = Z[:T, :, i, j].reshape(-1, hours_per_day, 4)
    e = log_rr[:T, i, j].reshape(-1, hours_per_day)
    ok = ~(np.isnan(z).any((1, 2)) | np.isnan(e).any(1))
    days = np.nonzero(ok)[0]
    return days, z[ok].mean(1), e[ok].mean(1)


def simulate_logs(user: SimUser, z_bar: np.ndarray, eta_bar: np.ndarray, k: float,
                  rng: np.random.Generator, integer: bool = True) -> np.ndarray:
    """Generate one severity log per day from the user's TRUE parameters.

    severity = baseline + k * logRR + w · z + noise, then (by default) rounded and
    clipped to the 0–4 scale a real user would tap in the app.
    """
    y = user.baseline + k * eta_bar + z_bar @ user.w + rng.normal(0, user.noise_sd, len(eta_bar))
    if integer:
        y = np.clip(np.rint(y), 0, 4)
    return y
