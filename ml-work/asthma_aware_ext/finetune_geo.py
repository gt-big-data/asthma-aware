"""Fine-tune a train_geo checkpoint on a new period (e.g. GEOS-CF v2, Aug 2025 on), or train
the same model from scratch on that period for comparison.

Fine-tuning keeps the checkpoint's normalization (input, target and geography statistics), so
the pretrained weights see inputs on the scale they were trained on, and starts from its
weights at a lower learning rate. ``--from-scratch`` instead fits normalization on the new
training weeks and starts from a fresh initialization, as ``train_geo`` does.

The weekly seasonal split runs over --start..--end (``splits.weekly_split_range``). Before
training, the starting model is scored on validation and test weeks, which for fine-tuning is
the "current model, no fine-tuning" result.

Usage (from ml-work):
    python -m asthma_aware_ext.finetune_geo --checkpoint runs/geo_v1/no2_best.pt \
        --config data_config_v2.json --start 2025-08-05 --end 2026-11-01 \
        --epochs 20 --learning-rate 1e-4 --output runs/geo_v2_finetune
    python -m asthma_aware_ext.finetune_geo --checkpoint runs/geo_v1/no2_best.pt \
        --config data_config_v2.json --start 2025-08-05 --end 2026-11-01 \
        --from-scratch --epochs 20 --learning-rate 1e-3 --output runs/geo_v2_scratch
"""

from __future__ import annotations

import argparse
import copy
import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, RandomSampler

from asthma_aware.data_pipeline import SourceDataset, _seed_worker, build_catalog, fit_normalization, load_config, masked_loss

from .evaluate import NormalizedView, format_score, load_checkpoint_model, score
from .geo_unet import ForecastGeographyConfig
from .geography import GeographyDataset, fit_geography_normalization, load_geography_config, read_geography
from .splits import apply_split, weekly_split_range

FORMAT = "asthma_aware_ext.train_geo"  # same checkpoint layout, so evaluate.py reads these too


def checkpoint_geography_normalization(payload, geo_cfg, grid):
    """The checkpoint's geography statistics, re-stamped for this copy of the raster.

    GeographyDataset refuses statistics fit on a file with another size or mtime (a guard
    against a changed raster). A fresh clone has a new mtime, so instead we recompute the
    statistics here and require them to match the checkpoint's before reusing them.
    """
    old = payload["geography_normalization"]
    data, names = read_geography(geo_cfg["path"], grid, geo_cfg.get("log1p_bands", []))
    new = fit_geography_normalization(data, names, geo_cfg["path"])
    if new["bands"] != old["bands"]:
        raise ValueError(f"Geography bands differ from the checkpoint: {new['bands']} vs {old['bands']}")
    if not (np.allclose(new["mean"], old["mean"], rtol=1e-5, atol=1e-6)
            and np.allclose(new["std"], old["std"], rtol=1e-5, atol=1e-6)):
        raise ValueError("Geography raster differs from the one the checkpoint was trained with")
    return dict(old, file=new["file"])


def make_loader(bundle, source, split, normalization, geo_cfg, geo_norm, *, seed, num_workers,
                samples_per_epoch=None, max_samples=None):
    base = NormalizedView(SourceDataset(bundle, source, split, None), normalization)
    dataset = GeographyDataset(base, geo_cfg, geo_norm)
    if split == "train":
        n = len(dataset) if samples_per_epoch is None else min(samples_per_epoch, len(dataset))
        sampler = RandomSampler(dataset, num_samples=n, generator=torch.Generator().manual_seed(seed))
        return DataLoader(dataset, batch_size=1, sampler=sampler, num_workers=num_workers,
                          worker_init_fn=_seed_worker)
    if max_samples is not None and len(dataset) > max_samples:
        step = len(dataset) / max_samples
        dataset = torch.utils.data.Subset(dataset, [int(i * step) for i in range(max_samples)])
    return DataLoader(dataset, batch_size=1, shuffle=False, num_workers=num_workers)


def _close(loader):
    ds = loader.dataset
    (ds.dataset if isinstance(ds, torch.utils.data.Subset) else ds).close()


def run(args):
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Using {device}", flush=True)
    payload = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    if payload.get("format") != FORMAT:
        raise ValueError("Expected a checkpoint from asthma_aware_ext.train_geo")
    source = payload["source"]
    geo_cfg = load_geography_config(args.geography_config)

    bundle = build_catalog(load_config(args.config))
    report = bundle["reports"][source]
    if not report["ready"]:
        raise RuntimeError(f"{source} not ready: {'; '.join(report['blockers'])}")
    mapping, split_summary = weekly_split_range(args.start, args.end, seed=args.seed)
    bundle, counts = apply_split(bundle, mapping)
    print("split:", json.dumps(split_summary["days"]), "| samples:", json.dumps(counts[source]), flush=True)

    raw = SourceDataset(bundle, source, "train")
    grid, channels = raw.grid, list(raw.channels)
    if args.from_scratch:
        print(f"{source}: from scratch; fitting normalization on {len(raw)} training samples", flush=True)
        normalization = fit_normalization(raw)
        data, names = read_geography(geo_cfg["path"], grid, geo_cfg.get("log1p_bands", []))
        geo_norm = fit_geography_normalization(data, names, geo_cfg["path"])
        config = ForecastGeographyConfig(forecast_channels=len(channels), geography_channels=len(names),
                                         target_names=tuple(payload["model_config"]["target_names"]), time_channels=4)
        torch.manual_seed(args.seed)
        model = config.build_model()
    else:
        print(f"{source}: fine-tuning {args.checkpoint} (epoch {payload['epoch']}), keeping its normalization",
              flush=True)
        normalization = payload["normalization"]
        if len(normalization["input_mean"]) != len(channels):
            raise ValueError("Checkpoint and data have a different number of forecast channels")
        geo_norm = checkpoint_geography_normalization(payload, geo_cfg, grid)
        model, _ = load_checkpoint_model(payload)
        config = ForecastGeographyConfig(**{k: tuple(v) if isinstance(v, list) else v
                                            for k, v in payload["model_config"].items()})
    raw.close()
    model = model.to(device)

    common = dict(seed=args.seed, num_workers=args.num_workers)
    train_loader = make_loader(bundle, source, "train", normalization, geo_cfg, geo_norm,
                               samples_per_epoch=args.samples_per_epoch, **common)
    val_loader = make_loader(bundle, source, "validation", normalization, geo_cfg, geo_norm,
                             max_samples=args.max_validation_samples, **common)
    test_loader = make_loader(bundle, source, "test", normalization, geo_cfg, geo_norm, **common)

    results = {"mode": "from_scratch" if args.from_scratch else "finetune", "source": source,
               "checkpoint": str(args.checkpoint), "split": split_summary, "split_counts": counts[source],
               "args": vars(args)}
    if not args.from_scratch:
        # The pretrained model as-is on the new period: the "no fine-tuning" baseline.
        results["start_validation"] = score(model, val_loader, normalization, source, device)
        results["start_test"] = score(model, test_loader, normalization, source, device)
        print(f"{source} | before fine-tuning | validation {format_score(results['start_validation'])}", flush=True)
        print(f"{source} | before fine-tuning | test {format_score(results['start_test'])}", flush=True)

    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    best_path = out_dir / f"{source}_best.pt"

    def save(epoch, val):
        torch.save({
            "format": FORMAT, "source": source, "epoch": epoch,
            "model_config": asdict(config), "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(), "normalization": normalization,
            "geography_normalization": geo_norm, "geography_config": geo_cfg, "split": split_summary,
            "source_config": bundle["config"]["sources"][source], "target": bundle["reports"][source]["target"],
            "target_units": bundle["reports"][source]["target_units"], "validation": val,
            "finetuned_from": None if args.from_scratch else str(args.checkpoint), "args": vars(args),
        }, best_path)

    history, best = [], None
    if not args.from_scratch:  # the starting point competes for "best" too
        best = dict(results["start_validation"], epoch=0)
        save(0, results["start_validation"])
    for epoch in range(1, args.epochs + 1):
        model.train()
        t0, total, updates = time.time(), 0.0, 0
        for batch in train_loader:
            batch = {k: v.to(device) if isinstance(v, torch.Tensor) else v for k, v in batch.items()}
            prediction = model(batch["forecast"], batch["geography"], batch["baseline"], batch["time_features"])
            loss = masked_loss(prediction, batch)
            if loss is None:
                continue
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            total += loss.item()
            updates += 1
        if updates == 0:
            raise RuntimeError(f"{source}: no training updates")
        val = score(model, val_loader, normalization, source, device)
        history.append({"epoch": epoch, "train_normalized_mse": total / updates, "validation": val,
                        "seconds": round(time.time() - t0, 1)})
        print(f"{source} | epoch {epoch:02d} | train normalized MSE {total / updates:.4f} | "
              f"validation {format_score(val)} | {time.time() - t0:.0f}s", flush=True)
        if best is None or val["rmse"] < best["rmse"]:
            best = dict(val, epoch=epoch)
            save(epoch, val)
        results.update(history=history, best_epoch=best["epoch"], validation_at_best=best)
        (out_dir / "metrics.json").write_text(json.dumps(results, indent=2))

    model.load_state_dict(torch.load(best_path, map_location=device, weights_only=False)["model_state"])
    results["test"] = score(model, test_loader, normalization, source, device)
    print(f"{source} | best epoch {best['epoch']} | test {format_score(results['test'])}", flush=True)
    for loader in (train_loader, val_loader, test_loader):
        _close(loader)
    (out_dir / "metrics.json").write_text(json.dumps(results, indent=2))
    return results


def main(argv=None):
    p = argparse.ArgumentParser(description="Fine-tune (or retrain from scratch) a train_geo model on a new period.")
    p.add_argument("--checkpoint", required=True, help="train_geo checkpoint (also gives the source and architecture)")
    p.add_argument("--config", required=True, help="data config for the new period")
    p.add_argument("--geography-config", default="geography_config.json")
    p.add_argument("--start", required=True, help="first day of the period (YYYY-MM-DD, UTC)")
    p.add_argument("--end", required=True, help="day after the last day (exclusive)")
    p.add_argument("--from-scratch", action="store_true", help="fresh weights and normalization fit on the new period")
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--learning-rate", type=float, default=None, help="default 1e-4 to fine-tune, 1e-3 from scratch")
    p.add_argument("--seed", type=int, default=2023)
    p.add_argument("--samples-per-epoch", type=int, default=None)
    p.add_argument("--max-validation-samples", type=int, default=None)
    p.add_argument("--num-workers", type=int, default=0)
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    if args.epochs < 1:
        p.error("--epochs must be positive")
    if args.learning_rate is None:
        args.learning_rate = 1e-3 if args.from_scratch else 1e-4
    return run(args)


if __name__ == "__main__":
    main()
