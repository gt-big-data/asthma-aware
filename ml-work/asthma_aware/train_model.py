"""Minimal independent-source training integration. Importing does not train."""

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
            target_names=(TARGETS[source],),
            geography_channels=0,
            time_channels=4,
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
        "product": "analysis",
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
        "product": "analysis",
        "forecast_skill_validated": False,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, path)


@torch.no_grad()
def evaluate(model, loader):
    model.eval()
    squared_error = 0.0
    count = 0
    for batch in loader:
        batch = {key: value.to(model.device) for key, value in batch.items()}

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

        if count == 0:
            raise RuntimeError("No valid validation observations")

        return squared_error / count

        

def train(epochs=20, sources=("temperature", "dewpoint"), learning_rate=1e-3):
    torch.manual_seed(42)
    prepared = Path("prepared")
    bundle = json.loads((prepared / "bundle.json").read_text())

    device = ("cuda" if torch.cuda.is_available() else "cpu")
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

                validation_mse = evaluate(model, validation_loader)
                validation_rmse_c = math.sqrt(validation_mse) * normalization["target_std"] # convert to Celsius
                print(
                    f"{source} | epoch {epoch:02d} | "
                    f"train normalized MSE={training['mean_batch_loss']:.4f} | "
                    f"validation RMSE={validation_rmse_c:.3f} °C"
                )

                if validation_mse < best_validation:
                    best_validation = validation_mse
                    save_source_checkpoint(
                        Path("checkpoints") / f"{source}_best.pt",
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
                