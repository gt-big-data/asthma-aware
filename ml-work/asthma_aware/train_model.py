"""Minimal independent-source training integration. Importing does not train."""

import argparse
import torch
from pathlib import Path
import math
import json
from .data_pipeline import TARGETS, masked_loss, make_loader
from .source_models import SourceModelConfig, build_source_models



def initialize_training(
    loaders, *, learning_rate=1e-3, device="cpu", architecture=None
):
    """One independent model/optimizer per ready loader; persist config/scalers separately."""
    architecture = architecture or {}
    configs = {
        source: SourceModelConfig(
            forecast_channels=len(loader.dataset.channels),
            target_names=(loader.dataset.report["target"],),
            geography_channels=0,
            time_channels=loader.dataset.time_channels,
            **architecture
        )
        for source, loader in loaders.items()
    }
    models = build_source_models(configs)
    optimizers = {}
    for source, model in models.items():
        model.to(device)
        optimizers[source] = torch.optim.Adam(model.parameters(), lr=learning_rate)
    return models, optimizers, configs


def train_one_epoch(model, loader, optimizer, device="cpu"):
    model.train()
    total = 0.0
    updates = 0
    for batch in loader:
        batch = {
            k: v.to(device) if isinstance(v, torch.Tensor) else v
            for k, v in batch.items()
        }
        optimizer.zero_grad(set_to_none=True)
        prediction = model(
            batch["forecast"],
            baseline=batch["baseline"],
            time_features=batch["time_features"],
        )
        loss = masked_loss(prediction, batch)
        if loss is None:
            continue
        loss.backward()
        optimizer.step()
        total += loss.item()
        updates += 1
    return {
        "mean_batch_loss": total / updates if updates else None,
        "updates": updates,
        "product": loader.dataset.product,
    }


def save_source_checkpoint(path, source, model, optimizer, config, loader, epoch):
    """Save one source's model and preprocessing provenance; no shared checkpoint."""
    from dataclasses import asdict
    from pathlib import Path
    from .data_pipeline import signature

    dataset = loader.dataset
    payload = {
        "source": source,
        "epoch": epoch,
        "model_config": asdict(config),
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "normalization": dataset.normalization,
        "source_config": dataset.cfg,
        "grid": dataset.grid.as_dict(),
        "split_dates": dataset.report["split_dates"],
        "data_signature": signature(dataset.bundle, source),
        "target": dataset.report["target"],
        "target_units": dataset.report["target_units"],
        "product": dataset.product,
        "forecast_skill_validated": False,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, path)


@torch.no_grad()
def evaluate(model, loader, return_details=False):
    model.eval()
    squared_error = 0.0
    count = 0
    horizons = {"0-24": [0.0, 0], "24-48": [0.0, 0], "48-72": [0.0, 0]}
    for batch in loader:
        device = next(model.parameters()).device
        batch = {
            key: value.to(device) if isinstance(value, torch.Tensor) else value
            for key, value in batch.items()
        }

        prediction = model(
            batch["forecast"],
            baseline=batch["baseline"],
            time_features=batch["time_features"]
        )
        loss = masked_loss(prediction, batch)

        if loss is None:
            continue

        n = int(batch["valid"].sum().item())
        squared_error += n * loss.item()
        count += n
        if "lead_time_hours" in batch:
            for i, lead in enumerate(batch["lead_time_hours"].tolist()):
                one = {k: v[i:i+1] if isinstance(v, torch.Tensor) else v for k,v in batch.items()}
                one_loss = masked_loss(prediction[i:i+1], one)
                if one_loss is not None:
                    valid_count = int((one["valid"] & torch.isfinite(one["targets"])).sum().item())
                    key = "0-24" if lead < 24 else "24-48" if lead < 48 else "48-72"
                    horizons[key][0] += one_loss.item()*valid_count
                    horizons[key][1] += valid_count

    if count == 0:
        raise RuntimeError("No valid validation observations")

    if return_details:
        return {"mse": squared_error/count, "valid_observations": count,
                "horizon_mse": {k: total/n if n else None for k,(total,n) in horizons.items()},
                "horizon_counts": {k: n for k,(_,n) in horizons.items()}}
    return squared_error / count

        

def train(epochs=20, sources=("temperature", "dewpoint"), learning_rate=1e-3, prepared_dir="prepared", checkpoint_dir="checkpoints"):
    if epochs < 1:
        raise ValueError("epochs must be positive")
    torch.manual_seed(42)
    prepared = Path(prepared_dir)
    bundle = json.loads((prepared / "prepared.json").read_text())

    device = ("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    print(f"Using {device}")

    for source in sources:
        normalization = json.loads((prepared / f"{source}_normalization.json").read_text())

        train_loader = make_loader(bundle, source, "train", normalization, batch_size=1, num_workers=0)
        validation_loader = make_loader(bundle, source, "validation", normalization, batch_size=1, num_workers=0)

        models, optimizers, configs = initialize_training(
            {source: train_loader},
            learning_rate=learning_rate,
            device=device
        )
        model = models[source]
        optimizer = optimizers[source]
        best_validation = float("inf")

        try:
            for epoch in range(1, epochs + 1):
                training = train_one_epoch(model, train_loader, optimizer, device=device)
                if training["updates"] == 0:
                    raise RuntimeError(f"{source}: no training updates")

                details = evaluate(model, validation_loader, return_details=True)
                validation_mse = details["mse"]
                if train_loader.dataset.product == "forecast":
                    for horizon, mse in details["horizon_mse"].items():
                        rmse = None if mse is None else math.sqrt(mse)*normalization["target_std"]
                        print(f"{source} | lead {horizon}h | RMSE={rmse} {train_loader.dataset.report['target_units']}")
                validation_rmse = math.sqrt(validation_mse) * normalization["target_std"]
                target_units = bundle["reports"][source]["target_units"]
                print(
                    f"{source} | epoch {epoch:02d} | "
                    f"train normalized MSE={training['mean_batch_loss']:.4f} | "
                    f"validation RMSE={validation_rmse:.6g} {target_units}"
                )

                metrics_path = Path(checkpoint_dir) / f"{source}_metrics.jsonl"
                metrics_path.parent.mkdir(parents=True, exist_ok=True)
                metrics = dict(details, source=source, epoch=epoch, product=train_loader.dataset.product,
                               target_units=target_units, rmse=validation_rmse,
                               horizon_rmse={k: None if v is None else math.sqrt(v)*normalization["target_std"]
                                             for k,v in details["horizon_mse"].items()})
                with metrics_path.open("a") as stream:
                    stream.write(json.dumps(metrics) + "\n")
                if validation_mse < best_validation:
                    best_validation = validation_mse
                    save_source_checkpoint(
                        Path(checkpoint_dir) / f"{source}_best.pt",
                        source,
                        model,
                        optimizer,
                        configs[source],
                        train_loader,
                        epoch,
                    )
        finally:
            train_loader.dataset.close()
            validation_loader.dataset.close()

        del model, optimizer, models, optimizers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train independent downscaling models.")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--sources", nargs="+", default=["temperature", "dewpoint"])
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--prepared", default="prepared")
    parser.add_argument("--checkpoints", default="checkpoints")
    args = parser.parse_args()
    train(epochs=args.epochs, sources=args.sources, learning_rate=args.learning_rate, prepared_dir=args.prepared, checkpoint_dir=args.checkpoints)
