"""Column-to-surface NO2 conversion and the scores used to judge it.

Satellites see the whole tropospheric NO2 column, not what people breathe. Chang et al. (2025,
npj Clim. Atmos. Sci. 8:1, doi:10.1038/s41612-024-00891-z) show with MAX-DOAS profiles that the
column-to-surface relationship is set mostly by vertical mixing: it holds when NO2 is spread
through the boundary layer and breaks when NO2 is trapped near the ground (low boundary layer,
cloudy and humid air, heavy local emissions).

We have no profile measurements, so GEOS-CF's own profile supplies the shape: its surface value
divided by its column says how much of the column sits at ground level in that hour and cell.
The satellite then sets the amount:

    surface ≈ satellite_column × (GEOS-CF surface / GEOS-CF column)
            = GEOS-CF surface × (satellite_column / GEOS-CF column)

The second form is how it is computed: GEOS-CF's surface value, corrected by how far the
satellite says GEOS-CF's column is off. The correction is clamped so a single noisy pixel or a
near-empty model column can't produce an extreme surface value.
"""

from __future__ import annotations

from typing import Dict

import numpy as np

AVOGADRO = 6.02214076e23
# Sentinel-5P L3 reports mol m-2; GEOS-CF's tropcol_no2 is in 1e15 molecules cm-2.
MOL_M2_TO_1E15_MOLEC_CM2 = AVOGADRO / 1e4 / 1e15

# Columns below this (1e15 molec cm-2) are near the TROPOMI noise floor over clean areas, so their
# ratio to the model says little; the conversion leaves GEOS-CF unchanged there.
MIN_COLUMN = 0.5
SCALE_LIMITS = (0.2, 5.0)


def column_scale(satellite_column, geos_column, *, min_column: float = MIN_COLUMN,
                 limits=SCALE_LIMITS) -> np.ndarray:
    """satellite / GEOS-CF column, clamped to `limits`; 1 where either column is too small to trust.

    Both columns in 1e15 molec cm-2. NaN where the satellite has no valid pixel (clouds, gaps).
    """
    sat = np.asarray(satellite_column, dtype=float)
    geos = np.asarray(geos_column, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.clip(sat / geos, *limits)
    trusted = (sat >= min_column) & (geos >= min_column)
    scale = np.where(trusted, scale, 1.0)
    return np.where(np.isfinite(sat) & np.isfinite(geos), scale, np.nan)


def column_to_surface(satellite_column, geos_surface, geos_column, **kwargs) -> np.ndarray:
    """Surface NO2 in GEOS-CF surface units, with GEOS-CF's profile and the satellite's amount."""
    return np.asarray(geos_surface, dtype=float) * column_scale(satellite_column, geos_column, **kwargs)


def scores(observed, estimate) -> Dict[str, float]:
    """Agreement with monitors: n, Pearson R, mean bias, MAE, and median observed/estimate ratio."""
    obs = np.asarray(observed, dtype=float)
    est = np.asarray(estimate, dtype=float)
    ok = np.isfinite(obs) & np.isfinite(est) & (obs > 0) & (est > 0)
    obs, est = obs[ok], est[ok]
    if len(obs) < 3:
        return {"n": int(len(obs)), "r": float("nan"), "bias": float("nan"), "mae": float("nan"),
                "median_ratio": float("nan")}
    return {
        "n": int(len(obs)),
        "r": float(np.corrcoef(obs, est)[0, 1]),
        "bias": float(np.mean(est - obs)),
        "mae": float(np.mean(np.abs(est - obs))),
        "median_ratio": float(np.median(obs / est)),
    }
