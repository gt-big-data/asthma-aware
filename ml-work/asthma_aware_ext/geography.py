"""Static fine-grid geography layers, added on top of the existing data pipeline.

Nothing in ``asthma_aware`` is modified. ``GeographyDataset`` wraps an existing
``SourceDataset`` and adds a ``"geography"`` tensor to each sample; the existing
dataset still does all matching, masking, and forecast/label normalization.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
import torch
from torch.utils.data import DataLoader, Dataset

from asthma_aware.data_pipeline import SourceDataset, _seed_worker


def load_geography_config(path):
    """Read a geography config; its raster path resolves relative to the file."""
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    cfg["path"] = str((path.parent / cfg["path"]).resolve())
    cfg.setdefault("log1p_bands", [])
    return cfg


def read_geography(path, grid, log1p_bands=()):
    """Static layers as ([C, H, W] float32, band names), on exactly ``grid``.

    Bands named in ``log1p_bands`` (by band description) are log1p-transformed,
    for heavy-tailed counts such as population or road length. No regridding is
    done: a raster on any other grid is an error, so layers cannot silently shift.
    """
    with rasterio.open(path) as src:
        names = list(src.descriptions)
        if any(not n for n in names):
            raise ValueError("Every geography band needs a description (its name)")
        if (src.width, src.height) != (grid.width, grid.height):
            raise ValueError(
                f"Geography raster is {src.width}x{src.height}; grid is {grid.width}x{grid.height}"
            )
        if not src.transform.almost_equals(grid.affine, precision=1e-9):
            raise ValueError("Geography raster transform does not match the grid")
        if src.crs != rasterio.crs.CRS.from_user_input(grid.crs):
            raise ValueError("Geography raster CRS does not match the grid")
        data = src.read(masked=True).astype(np.float32).filled(np.nan)
    unknown = set(log1p_bands) - set(names)
    if unknown:
        raise ValueError(f"log1p_bands names unknown bands: {sorted(unknown)}")
    for i, name in enumerate(names):
        if name in log1p_bands:
            if np.nanmin(data[i]) < 0:
                raise ValueError(f"Cannot log1p negative values in {name}")
            data[i] = np.log1p(data[i])
    if not np.isfinite(data).all():
        raise ValueError("Geography raster has missing or non-finite values")
    return data, names


def fit_geography_normalization(data, names, path):
    """Per-band mean/std over the whole raster. It holds no labels, so no leakage."""
    flat = data.reshape(len(data), -1).astype(np.float64)
    std = flat.std(1)
    stat = Path(path).stat()
    return dict(
        bands=list(names),
        mean=flat.mean(1).tolist(),
        std=np.where(std > 0, std, 1.0).tolist(),
        file={"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
    )


class GeographyDataset(Dataset):
    """An existing ``SourceDataset`` plus one static, normalized geography tensor."""

    def __init__(self, base: SourceDataset, geo_cfg, geo_normalization=None):
        self.base = base
        self.data, self.names = read_geography(
            geo_cfg["path"], base.grid, geo_cfg.get("log1p_bands", [])
        )
        if geo_normalization is not None:
            if geo_normalization["bands"] != self.names:
                raise ValueError("Geography normalization was fit on different bands")
            stat = Path(geo_cfg["path"]).stat()
            fit = geo_normalization["file"]
            if (stat.st_size, stat.st_mtime_ns) != (fit["size"], fit["mtime_ns"]):
                raise ValueError("Geography raster changed since normalization was fit")
            mean = np.array(geo_normalization["mean"], dtype=np.float32)[:, None, None]
            std = np.array(geo_normalization["std"], dtype=np.float32)[:, None, None]
            self.data = (self.data - mean) / std
        self.tensor = torch.from_numpy(self.data.astype(np.float32))
        self.geo_cfg = geo_cfg
        self.geo_normalization = geo_normalization

    @property
    def channels(self):
        return self.base.channels

    @property
    def geography_channels(self):
        return len(self.names)

    def __len__(self):
        return len(self.base)

    def __getitem__(self, i):
        sample = self.base[i]
        sample["geography"] = self.tensor
        return sample

    def close(self):
        self.base.close()


def make_geography_loader(
    bundle, source, split, normalization, geo_cfg, geo_normalization,
    *, batch_size=1, num_workers=0, seed=42,
):
    """Same loader settings as ``asthma_aware.data_pipeline.make_loader``, plus geography."""
    if normalization is None or geo_normalization is None:
        raise ValueError("Pass training-fitted normalization explicitly")
    dataset = GeographyDataset(
        SourceDataset(bundle, source, split, normalization), geo_cfg, geo_normalization
    )
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=split == "train",
        num_workers=num_workers,
        generator=torch.Generator().manual_seed(seed),
        worker_init_fn=_seed_worker,
    )
