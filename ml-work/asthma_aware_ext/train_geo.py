"""Train one independent geography-aware model per source on the weekly seasonal split.

New file; ``asthma_aware`` (data pipeline, ResidualUNet, train_model) is used unchanged.
Differences from ``asthma_aware.train_model``:
  * season-balanced weekly-block split (``splits.py``) instead of a chronological cutoff;
  * static geography layers, fed with the upsampled forecast at full resolution;
  * errors printed in each source's own units (umol/m2 or degC) with enough precision,
    next to the raw GEOS-CF error on the same labels;
  * the best-validation checkpoint is scored on the test set once training ends.

Usage (from ml-work):
    python -m asthma_aware_ext.train_geo --config data_config_fullyear.json \
        --sources temperature dewpoint no2 --epochs 20 --output runs/geo_v1
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import torch
from torch.utils.data import DataLoader, RandomSampler

from asthma_aware.data_pipeline import (
    TARGETS,
    SourceDataset,
    _seed_worker,
    build_catalog,
    fit_normalization,
    load_config,
    masked_loss,
)

from .evaluate import format_score, score
from .geo_unet import ForecastGeographyConfig
from .geography import GeographyDataset, fit_geography_normalization, load_geography_config, read_geography
from .splits import apply_split, weekly_split

FORMAT = "asthma_aware_ext.train_geo"


def prepare(config_path, seed):
    bundle = build_catalog(load_config(config_path))
    mapping, summary = weekly_split(seed=seed)
    bundle, counts = apply_split(bundle, mapping)
    return bundle, summary, counts


def loader_for(bundle, source, split, normalization, geo_cfg, geo_norm, *, seed, num_workers,
               samples_per_epoch=None, max_samples=None):
    dataset = GeographyDataset(SourceDataset(bundle, source, split, normalization), geo_cfg, geo_norm)
    generator = torch.Generator().manual_seed(seed)
    if split == "train":
        n = len(dataset) if samples_per_epoch is None else min(samples_per_epoch, len(dataset))
        sampler = RandomSampler(dataset, num_samples=n, generator=generator)
        return DataLoader(dataset, batch_size=1, sampler=sampler, num_workers=num_workers,
                          worker_init_fn=_seed_worker)
    if max_samples is not None and len(dataset) > max_samples:
        # Fixed, evenly spaced subset so every epoch is scored on the same samples.
        step = len(dataset) / max_samples
        dataset = torch.utils.data.Subset(dataset, [int(i * step) for i in range(max_samples)])
    return DataLoader(dataset, batch_size=1, shuffle=False, num_workers=num_workers)


def train_source(bundle, source, geo_cfg, split_summary, args, out_dir, device):
    raw = SourceDataset(bundle, source, "train")
    print(f"{source}: fitting normalization on {len(raw)} training samples", flush=True)
    normalization = fit_normalization(raw)
    data, names = read_geography(geo_cfg["path"], raw.grid, geo_cfg.get("log1p_bands", []))
    geo_norm = fit_geography_normalization(data, names, geo_cfg["path"])
    raw.close()

    common = dict(seed=args.seed, num_workers=args.num_workers)
    train_loader = loader_for(bundle, source, "train", normalization, geo_cfg, geo_norm,
                              samples_per_epoch=args.samples_per_epoch, **common)
    val_loader = loader_for(bundle, source, "validation", normalization, geo_cfg, geo_norm,
                            max_samples=args.max_validation_samples, **common)
    config = ForecastGeographyConfig(
        forecast_channels=len(raw.channels),
        geography_channels=len(names),
        target_names=(TARGETS[source],),
        time_channels=4,
    )
    torch.manual_seed(args.seed)
    model = config.build_model().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)

    def payload(epoch, val):
        return {
            "format": FORMAT,
            "source": source,
            "epoch": epoch,
            "model_config": asdict(config),
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "normalization": normalization,
            "geography_normalization": geo_norm,
            "geography_config": geo_cfg,
            "split": split_summary,
            "source_config": bundle["config"]["sources"][source],
            "target": bundle["reports"][source]["target"],
            "target_units": bundle["reports"][source]["target_units"],
            "validation": val,
            "args": vars(args),
        }

    history, best, best_path = [], None, out_dir / f"{source}_best.pt"
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
            torch.save(payload(epoch, val), best_path)

    # Score the best-validation checkpoint on the untouched test weeks.
    model.load_state_dict(torch.load(best_path, map_location=device, weights_only=False)["model_state"])
    test_loader = loader_for(bundle, source, "test", normalization, geo_cfg, geo_norm, **common)
    test = score(model, test_loader, normalization, source, device)
    print(f"{source} | best epoch {best['epoch']} | test {format_score(test)}", flush=True)
    for loader in (train_loader, val_loader, test_loader):
        ds = loader.dataset
        (ds.dataset if isinstance(ds, torch.utils.data.Subset) else ds).close()
    return {"best_epoch": best["epoch"], "validation_at_best": best, "test": test, "history": history,
            "train_samples": len(train_loader.dataset)}


def main(argv=None):
    p = argparse.ArgumentParser(description="Train geography-aware per-source models (weekly seasonal split).")
    p.add_argument("--config", required=True)
    p.add_argument("--geography-config", default="geography_config.json")
    p.add_argument("--sources", nargs="+", choices=["temperature", "dewpoint", "no2"],
                   default=["temperature", "dewpoint", "no2"])
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--learning-rate", type=float, default=1e-3)
    p.add_argument("--seed", type=int, default=2023, help="split, sampling and initialization seed")
    p.add_argument("--samples-per-epoch", type=int, default=None,
                   help="random training samples per epoch (hourly station sources have ~8k)")
    p.add_argument("--max-validation-samples", type=int, default=None,
                   help="score validation on a fixed evenly spaced subset of this size")
    p.add_argument("--num-workers", type=int, default=0)
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    if args.epochs < 1:
        p.error("--epochs must be positive")

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Using {device}", flush=True)
    geo_cfg = load_geography_config(args.geography_config)
    bundle, split_summary, counts = prepare(args.config, args.seed)
    print("split:", json.dumps(split_summary["days"]), flush=True)
    results = {"split": split_summary, "split_counts": counts}
    for source in args.sources:
        report = bundle["reports"][source]
        if not report["ready"]:
            print(f"{source}: skipped, not ready: {'; '.join(report['blockers'])}", flush=True)
            results[source] = {"skipped": report["blockers"]}
            continue
        results[source] = train_source(bundle, source, geo_cfg, split_summary, args, out_dir, device)
        (out_dir / "metrics.json").write_text(json.dumps(results, indent=2))
    (out_dir / "metrics.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
