"""Minimal independent-source training integration. Importing does not train."""

import torch
from .data_pipeline import TARGETS, masked_loss
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
