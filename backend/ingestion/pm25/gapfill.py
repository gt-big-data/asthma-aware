"""Gap filling for a daily (days, height, width) cube, and an honest way to pick a method.

The comparison follows the ML team's recipe: hide values we know, let each method fill
them without seeing them, and score mean absolute error at the hidden cells only. Hidden
patterns mimic real gaps (runs of days, patches of cells, whole missing days), and several
seeds check the ranking is stable.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional

import numpy as np
from scipy.interpolate import griddata
from scipy.spatial import QhullError

HIDE_PATTERNS = ("random_5pct", "consecutive_days", "spatial_patches", "whole_days")


def _time_interp(cube: np.ndarray, nearest: bool) -> np.ndarray:
    out = cube.copy()
    t = np.arange(out.shape[0])
    for i in range(out.shape[1]):
        for j in range(out.shape[2]):
            s = out[:, i, j]
            m = np.isnan(s)
            if not m.any() or (~m).sum() < 2:
                continue
            known = t[~m]
            if nearest:
                k = np.searchsorted(known, t[m]).clip(1, len(known) - 1)
                left, right = known[k - 1], known[k]
                pick = np.where(np.abs(t[m] - left) <= np.abs(right - t[m]), left, right)
                s[m] = s[pick]
            else:
                s[m] = np.interp(t[m], known, s[~m])
    return out


def time_linear(cube: np.ndarray) -> np.ndarray:
    """Straight line between the previous and next known day at each cell."""
    return _time_interp(cube, nearest=False)


def time_nearest(cube: np.ndarray) -> np.ndarray:
    """Copy the nearest known day at each cell."""
    return _time_interp(cube, nearest=True)


def _spatial(cube: np.ndarray, how: str) -> np.ndarray:
    out = cube.copy()
    _, h, w = out.shape
    yy, xx = np.mgrid[0:h, 0:w]
    for d in range(out.shape[0]):
        f = out[d]
        m = np.isnan(f)
        if not m.any() or (~m).sum() < 4:
            continue
        pts = np.c_[yy[~m], xx[~m]]
        v = f[~m]
        if how == "idw":
            q = np.c_[yy[m], xx[m]]
            dist = np.sqrt(((q[:, None, :] - pts[None]) ** 2).sum(-1))
            wgt = 1 / dist**2
            f[m] = (wgt * v).sum(1) / wgt.sum(1)
        else:
            try:
                g = griddata(pts, v, (yy[m], xx[m]), method=how)
            except QhullError:  # known cells all on one line: no triangles to interpolate in
                g = np.full(int(m.sum()), np.nan)
            bad = np.isnan(g)
            if bad.any():  # outside the convex hull of known cells
                g[bad] = griddata(pts, v, (yy[m][bad], xx[m][bad]), method="nearest")
            f[m] = g
    return out


def space_linear(cube: np.ndarray) -> np.ndarray:
    """Blend between neighbouring cells on the same day."""
    return _spatial(cube, "linear")


def space_idw(cube: np.ndarray) -> np.ndarray:
    """Inverse-distance-weighted average of known cells on the same day."""
    return _spatial(cube, "idw")


def space_time_average(cube: np.ndarray) -> np.ndarray:
    """Mean of the spatial and temporal linear fills; falls back to whichever exists."""
    a, b = space_linear(cube), time_linear(cube)
    return np.where(np.isnan(a), b, np.where(np.isnan(b), a, (a + b) / 2))


METHODS: Dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "time_linear": time_linear,
    "time_nearest": time_nearest,
    "space_linear": space_linear,
    "space_idw": space_idw,
    "space_time_average": space_time_average,
}


def fill_remaining(cube: np.ndarray) -> np.ndarray:
    """Whatever a method could not reach (a whole missing day, an empty column): time-linear,
    then spatial nearest. Leaves NaN only if the cube has nothing to work from at all."""
    cube = np.where(np.isnan(cube), time_linear(cube), cube)
    return np.where(np.isnan(cube), _spatial(cube, "nearest"), cube)


def fill(cube: np.ndarray, method: str) -> np.ndarray:
    return fill_remaining(METHODS[method](cube))


def hide(cube: np.ndarray, kind: str, rng: np.random.Generator) -> np.ndarray:
    """A boolean mask of known cells to hide, shaped like one kind of real gap."""
    known = ~np.isnan(cube)
    t, h, w = cube.shape
    m = np.zeros(cube.shape, dtype=bool)
    if kind == "random_5pct":
        m = rng.random(cube.shape) < 0.05
    elif kind == "consecutive_days":  # 3-10 day runs at single cells
        for _ in range(max(1, t * h * w // 450)):
            d, i, j = rng.integers(0, max(1, t - 10)), rng.integers(h), rng.integers(w)
            m[d : d + rng.integers(3, 11), i, j] = True
    elif kind == "spatial_patches":  # 4x4 cell patches on single days
        size = min(4, h, w)
        for _ in range(max(1, t * h * w // 450)):
            d, i, j = rng.integers(t), rng.integers(h - size + 1), rng.integers(w - size + 1)
            m[d, i : i + size, j : j + size] = True
    elif kind == "whole_days":
        for d in rng.choice(t, min(15, max(1, t // 24)), replace=False):
            m[d] = True
    else:
        raise ValueError(f"unknown hide pattern {kind!r}")
    return m & known


def score_methods(
    cube: np.ndarray,
    patterns: Iterable[str] = HIDE_PATTERNS,
    seeds: int = 5,
    methods: Optional[Iterable[str]] = None,
) -> Dict[str, object]:
    """MAE of every method under every hide pattern, averaged over seeds.

    Returns {"by_pattern": {pattern: {method: {"mean", "std"}}}, "overall": {method: mae}, "best": method}.
    """
    names: List[str] = list(methods or METHODS)
    by_pattern: Dict[str, Dict[str, List[float]]] = {}
    for kind in patterns:
        for seed in range(seeds):
            mask = hide(cube, kind, np.random.default_rng(seed))
            if not mask.any():
                continue
            test = cube.copy()
            test[mask] = np.nan
            for name in names:
                pred = fill(test, name)
                mae = float(np.nanmean(np.abs(pred[mask] - cube[mask])))
                by_pattern.setdefault(kind, {}).setdefault(name, []).append(mae)
    summary = {k: {n: {"mean": float(np.mean(v)), "std": float(np.std(v))} for n, v in r.items()} for k, r in by_pattern.items()}
    overall = {n: float(np.mean([summary[k][n]["mean"] for k in summary])) for n in names}
    return {"by_pattern": summary, "overall": overall, "best": min(overall, key=overall.get)}
