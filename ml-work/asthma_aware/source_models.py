"""Build independent U-Nets for sources with different label availability.

Models never share weights. Their batches, dates, and output grids may differ.
Each source's forecast inputs and labels still need to be matched within it.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .unet import ResidualUNet


@dataclass(frozen=True)
class SourceModelConfig:
    """Architecture settings for one source; no data dimensions are fixed.

    A source may supervise one or several targets. Different sources may predict
    the same target with independent models. Forecast channels may include any
    forecast-safe predictors, not just the source's output variables.

    Use ``dataclasses.asdict(config)`` to save these settings alongside a model's
    state_dict. Dates, units, scalers, grid definitions, and splits belong to the
    source's data pipeline and should also be recorded when training is added.
    """

    forecast_channels: int
    target_names: tuple[str, ...]
    geography_channels: int = 0
    encoder_channels: tuple[int, ...] = (32, 64, 128, 256)
    forecast_encoder_channels: tuple[int, ...] = (32, 64)
    head_channels: int = 32
    time_channels: int = 0
    zero_init_residual: bool = True

    def build_model(self) -> ResidualUNet:
        """Create a fresh model, validating settings through ResidualUNet."""
        return ResidualUNet(**asdict(self))


def build_source_models(
    configs: Mapping[str, SourceModelConfig],
) -> dict[str, ResidualUNet]:
    """Return one separately initialized model per source identifier.

    The result is a plain dictionary so each model can have its own optimizer,
    loader, normalization, evaluation splits, and checkpoint. No joint forward
    pass or intersection of dates across sources is required. Reusing a config
    object for multiple sources still creates completely separate parameters.

    Set geography_channels=0 (the default) for the forecast-only architecture.
    Each model otherwise has the same forward contract as ResidualUNet.
    """
    if not configs:
        raise ValueError("configs must contain at least one data source")
    for source, config in configs.items():
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source identifiers must be nonempty strings")
        if not isinstance(config, SourceModelConfig):
            raise TypeError(f"config for {source!r} must be a SourceModelConfig")
    return {source: config.build_model() for source, config in configs.items()}
