"""Score models and the raw GEOS-CF baseline in physical units.

Works for checkpoints from ``asthma_aware_ext.train_geo`` and from the existing
``asthma_aware.train_model`` (whose printout labels every source in degrees C and rounds
NO2 errors, ~1e-5 mol/m2, to 0.000). Nothing in ``asthma_aware`` is modified.

Usage (from ml-work):
    python -m asthma_aware_ext.evaluate --checkpoint runs/x/no2_best.pt \
        --config data_config_fullyear.json --geography-config geography_config.json --split test
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

from asthma_aware.data_pipeline import SourceDataset, build_catalog, load_config
from asthma_aware.unet import ResidualUNet

from .geo_unet import ForecastGeographyConfig
from .geography import GeographyDataset, load_geography_config
from .splits import apply_split, weekly_split

# Labels and baselines are mol/m2 for satellite columns and degC for stations.
DISPLAY = {
    "no2": (1e6, "umol/m2"),
    "o3": (1e6, "umol/m2"),
    "temperature": (1.0, "degC"),
    "dewpoint": (1.0, "degC"),
}


class NormalizedView(Dataset):
    """Apply a given normalization to an unnormalized SourceDataset.

    Same arithmetic as ``SourceDataset.__getitem__`` with ``normalization`` set, but without
    its check that the stats were fit on this exact catalog, so a checkpoint can be scored
    on a different split. Stats still come only from that checkpoint's training data.
    """

    def __init__(self, base: SourceDataset, normalization):
        if base.normalization is not None:
            raise ValueError("Base dataset must be unnormalized")
        if len(normalization["input_mean"]) != len(base.channels):
            raise ValueError("Normalization has a different number of input channels")
        self.base = base
        self.n = normalization
        self.grid = base.grid
        self.channels = base.channels
        self._in_mean = torch.tensor(normalization["input_mean"], dtype=torch.float32)[:, None, None]
        self._in_std = torch.tensor(normalization["input_std"], dtype=torch.float32)[:, None, None]

    def __len__(self):
        return len(self.base)

    def __getitem__(self, i):
        s = self.base[i]
        tm, ts = self.n["target_mean"], self.n["target_std"]
        s["forecast"] = (s["forecast"] - self._in_mean) / self._in_std
        s["baseline"] = (s["baseline"] - tm) / ts
        s["targets"] = torch.where(s["valid"], (s["targets"] - tm) / ts, torch.zeros_like(s["targets"]))
        return s

    def close(self):
        self.base.close()


def _select(prediction, batch):
    """Values at labelled locations: station pixels, or the whole field for rasters."""
    if "rows" in batch:
        b = torch.arange(prediction.shape[0], device=prediction.device)[:, None]
        return prediction[b, 0, batch["rows"], batch["cols"]].unsqueeze(-1)
    return prediction


@torch.no_grad()
def score(model, loader, normalization, source, device="cpu", uses_geography=True):
    """Model and GEOS-CF-baseline errors over every valid label, in display units.

    Also reports per-sample RMSE averaged over samples, which weighs each hour/day
    equally (the label-weighted number favors days with many observed pixels).
    """
    factor, units = DISPLAY[source]
    scale = normalization["target_std"] * factor
    sums = {k: 0.0 for k in ("model_se", "base_se", "model_e", "base_e")}
    count = 0
    per_sample = {"model": [], "base": []}
    if model is not None:
        model.eval()
    for batch in loader:
        batch = {k: v.to(device) if isinstance(v, torch.Tensor) else v for k, v in batch.items()}
        valid = batch["valid"] & torch.isfinite(batch["targets"])
        if not valid.any():
            continue
        base = _select(batch["baseline"], batch)
        if model is not None:
            args = (batch["forecast"], batch["geography"]) if uses_geography else (batch["forecast"], None)
            pred = _select(model(*args, batch["baseline"], batch["time_features"]), batch)
        else:
            pred = base
        for i in range(valid.shape[0]):
            v = valid[i]
            if not v.any():
                continue
            t = batch["targets"][i][v].double()
            em = (pred[i][v].double() - t) * scale
            eb = (base[i][v].double() - t) * scale
            sums["model_se"] += float((em ** 2).sum())
            sums["base_se"] += float((eb ** 2).sum())
            sums["model_e"] += float(em.sum())
            sums["base_e"] += float(eb.sum())
            count += int(v.sum())
            per_sample["model"].append(math.sqrt(float((em ** 2).mean())))
            per_sample["base"].append(math.sqrt(float((eb ** 2).mean())))
    if count == 0:
        raise RuntimeError("No valid labels to score")
    out = {
        "units": units,
        "labels": count,
        "samples": len(per_sample["model"]),
        "rmse": math.sqrt(sums["model_se"] / count),
        "bias": sums["model_e"] / count,
        "geos_rmse": math.sqrt(sums["base_se"] / count),
        "geos_bias": sums["base_e"] / count,
        "mean_sample_rmse": float(np.mean(per_sample["model"])),
        "geos_mean_sample_rmse": float(np.mean(per_sample["base"])),
    }
    out["skill_vs_geos"] = 1.0 - out["rmse"] / out["geos_rmse"] if out["geos_rmse"] > 0 else None
    return out


def format_score(s):
    return (f"RMSE {s['rmse']:.4g} vs GEOS-CF {s['geos_rmse']:.4g} {s['units']} "
            f"(bias {s['bias']:+.3g} vs {s['geos_bias']:+.3g}; "
            f"skill {s['skill_vs_geos']:+.1%}; {s['samples']} samples, {s['labels']} labels)")


def load_checkpoint_model(payload, device="cpu"):
    """Rebuild the model from either checkpoint format; returns (model, uses_geography)."""
    cfg = dict(payload["model_config"])
    for key in ("target_names", "encoder_channels", "forecast_encoder_channels"):
        cfg[key] = tuple(cfg[key])
    if payload.get("format") == "asthma_aware_ext.train_geo":
        model = ForecastGeographyConfig(**cfg).build_model()
        uses_geography = True
    else:
        model = ResidualUNet(**cfg)
        uses_geography = cfg.get("geography_channels", 0) > 0
        if uses_geography:
            raise ValueError("Existing-format checkpoints with geography are not supported here")
    model.load_state_dict(payload["model_state"])
    return model.to(device).eval(), uses_geography


def evaluation_loader(bundle, source, split, payload, geo_cfg, batch_size=1, num_workers=0):
    base = NormalizedView(SourceDataset(bundle, source, split, None), payload["normalization"])
    if payload.get("format") == "asthma_aware_ext.train_geo":
        dataset = GeographyDataset(base, geo_cfg, payload["geography_normalization"])
    else:
        dataset = base
    return DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--config", required=True, help="data config (e.g. data_config_fullyear.json)")
    p.add_argument("--geography-config", default="geography_config.json")
    p.add_argument("--split", default="test", choices=["train", "validation", "test"])
    p.add_argument("--seed", type=int, default=None, help="weekly-split seed; default: the checkpoint's, else 2023")
    p.add_argument("--output", default=None, help="write the scores as JSON here")
    a = p.parse_args(argv)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    payload = torch.load(a.checkpoint, map_location="cpu", weights_only=False)
    source = payload["source"]
    seed = a.seed if a.seed is not None else payload.get("split", {}).get("seed", 2023)
    mapping, summary = weekly_split(seed=seed)
    bundle, _ = apply_split(build_catalog(load_config(a.config)), mapping)
    geo_cfg = load_geography_config(a.geography_config) if payload.get("format") == "asthma_aware_ext.train_geo" else None
    model, uses_geo = load_checkpoint_model(payload, device)
    loader = evaluation_loader(bundle, source, a.split, payload, geo_cfg)
    result = score(model, loader, payload["normalization"], source, device, uses_geo)
    result.update(source=source, split=a.split, split_seed=seed, checkpoint=str(a.checkpoint))
    print(f"{source} {a.split}: {format_score(result)}")
    if a.output:
        Path(a.output).write_text(json.dumps(result, indent=2))
    loader.dataset.close()


if __name__ == "__main__":
    main()
