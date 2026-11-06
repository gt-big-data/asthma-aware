"""Residual U-Net with optional geography and configurable target branches.

All spatial dimensions are inferred at runtime. Geographic alignment, target
definitions, normalization, and observation sampling belong in the data pipeline.
"""

from collections.abc import Sequence

import torch
from torch import Tensor, nn
from torch.nn import functional as F


class _ConvBlock(nn.Sequential):
    def __init__(self, in_channels: int, out_channels: int) -> None:
        # GroupNorm supports small batches and does not track running statistics.
        super().__init__(
            nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1, bias=False),
            nn.GroupNorm(1, out_channels),
            nn.SiLU(),
            nn.Conv2d(out_channels, out_channels, kernel_size=3, padding=1, bias=False),
            nn.GroupNorm(1, out_channels),
            nn.SiLU(),
        )


def _positive_int(name: str, value: int, minimum: int = 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}, got {value!r}")


class ResidualUNet(nn.Module):
    """Predict per-target corrections to an externally aligned fine-grid baseline.

    Args:
        forecast_channels: Number of coarse forecast input channels.
        geography_channels: Number of fine-grid geographic input channels. Set
            to zero to feed interpolated forecasts into the spatial encoder.
        target_names: Unique target names, in baseline and output channel order.
            Names are metadata; they do not prescribe units or physical quantities.
        encoder_channels: Spatial encoder widths, from finest to coarsest scale.
            Its length sets U-Net depth; each subsequent level halves spatial size.
        forecast_encoder_channels: Widths of the coarse CNN, which preserves its
            input resolution. Both encoder width sequences require values >= 2.
        head_channels: Hidden width of each independent output branch.
        time_channels: Number of optional time/lead features; zero disables them.
        zero_init_residual: Initialize each final convolution to zero, so the
            initial prediction equals the supplied baseline exactly.

    Forward inputs:
        forecast: [B, forecast_channels, Hc, Wc]. Must cover the same geographic
            extent and axis orientation as baseline. CNN feature interpolation
            assumes coextensive grids; it is not geographic regridding.
        geography: [B, geography_channels, H, W], required when geography_channels
            is positive. Omit or pass None when geography_channels is zero.
        baseline: [B, len(target_names), H, W]. Already regridded to the output
            grid, in the same physical quantities and numerical scaling as labels.
            Defines output dimensions in both modes and is always required.
        time_features: [B, time_channels], required exactly when time_channels > 0.

    Returns:
        [B, len(target_names), H, W], baseline plus learned corrections. No output
        activation is imposed because units and valid ranges are target-dependent.

    Inputs must be finite floating-point tensors on the same device and with the
    same dtype. Missing-data masks/imputation are the caller's responsibility.
    H and W must each be at least 2 ** (len(encoder_channels) - 1).
    """

    def __init__(
        self,
        forecast_channels: int,
        geography_channels: int,
        target_names: Sequence[str],
        *,
        encoder_channels: Sequence[int] = (32, 64, 128, 256),
        forecast_encoder_channels: Sequence[int] = (32, 64),
        head_channels: int = 32,
        time_channels: int = 0,
        zero_init_residual: bool = True,
    ) -> None:
        super().__init__()
        _positive_int("forecast_channels", forecast_channels)
        _positive_int("geography_channels", geography_channels, minimum=0)
        _positive_int("head_channels", head_channels)
        _positive_int("time_channels", time_channels, minimum=0)
        if isinstance(target_names, str):
            raise ValueError("target_names must be a sequence of names, not a string")
        targets = tuple(target_names)
        if not targets or any(not isinstance(name, str) or not name.strip() for name in targets):
            raise ValueError("target_names must contain nonempty strings")
        if len(set(targets)) != len(targets):
            raise ValueError("target_names must be unique")
        widths = tuple(encoder_channels)
        forecast_widths = tuple(forecast_encoder_channels)
        if len(widths) < 2:
            raise ValueError("encoder_channels must contain at least two levels")
        if not forecast_widths:
            raise ValueError("forecast_encoder_channels must contain at least one level")
        for name, values in (
            ("encoder_channels", widths),
            ("forecast_encoder_channels", forecast_widths),
        ):
            for value in values:
                _positive_int(name, value, minimum=2)

        self.forecast_channels = forecast_channels
        self.geography_channels = geography_channels
        self.target_names = targets
        self.time_channels = time_channels
        self.min_grid_size = 2 ** (len(widths) - 1)

        self.forecast_encoder = nn.Sequential(
            *[
                _ConvBlock(in_width, out_width)
                for in_width, out_width in zip(
                    (forecast_channels, *forecast_widths[:-1]), forecast_widths
                )
            ]
        )
        spatial_input_channels = geography_channels or forecast_channels
        self.spatial_encoder = nn.ModuleList(
            [
                _ConvBlock(in_width, out_width)
                for in_width, out_width in zip(
                    (spatial_input_channels, *widths[:-1]), widths
                )
            ]
        )
        self.pool = nn.MaxPool2d(kernel_size=2)
        self.fusion = _ConvBlock(
            widths[-1] + forecast_widths[-1] + time_channels, widths[-1]
        )
        self.decoder = nn.ModuleList(
            [
                _ConvBlock(widths[level + 1] + widths[level], widths[level])
                for level in reversed(range(len(widths) - 1))
            ]
        )
        # A ModuleList allows arbitrary target labels (including punctuation).
        self.heads = nn.ModuleList(
            [
                nn.Sequential(
                    nn.Conv2d(widths[0], head_channels, kernel_size=3, padding=1),
                    nn.SiLU(),
                    nn.Conv2d(head_channels, 1, kernel_size=1),
                )
                for _ in targets
            ]
        )
        if zero_init_residual:
            for head in self.heads:
                nn.init.zeros_(head[-1].weight)
                nn.init.zeros_(head[-1].bias)

    def _validate_inputs(
        self,
        forecast: Tensor,
        geography: Tensor | None,
        baseline: Tensor,
        time_features: Tensor | None,
    ) -> None:
        inputs = [
            ("forecast", forecast, self.forecast_channels),
            ("baseline", baseline, len(self.target_names)),
        ]
        if self.geography_channels:
            if geography is None:
                raise ValueError("geography is required when geography_channels is positive")
            inputs.append(("geography", geography, self.geography_channels))
        elif geography is not None:
            raise ValueError("geography must be omitted when geography_channels is zero")
        for name, tensor, channels in inputs:
            if tensor.ndim != 4 or tensor.shape[1] != channels:
                raise ValueError(f"{name} must have shape [B, {channels}, H, W]")
            if any(size < 1 for size in tensor.shape):
                raise ValueError(f"{name} must have nonempty batch and spatial dimensions")
            if not tensor.is_floating_point():
                raise ValueError(f"{name} must be floating point")
        if any(tensor.shape[0] != baseline.shape[0] for _, tensor, _ in inputs):
            raise ValueError("all inputs must have the same batch size")
        if geography is not None and baseline.shape[-2:] != geography.shape[-2:]:
            raise ValueError("baseline must already match the geography output grid")
        if min(baseline.shape[-2:]) < self.min_grid_size:
            raise ValueError(
                f"output grid height and width must each be >= {self.min_grid_size} "
                "for the configured encoder depth"
            )
        if self.time_channels:
            expected = (baseline.shape[0], self.time_channels)
            if time_features is None or tuple(time_features.shape) != expected:
                raise ValueError(f"time_features must have shape {expected}")
        elif time_features is not None:
            raise ValueError("time_features provided but time_channels is zero")
        tensors = [tensor for _, tensor, _ in inputs]
        if time_features is not None:
            tensors.append(time_features)
        for tensor in tensors:
            if tensor.device != baseline.device or tensor.dtype != baseline.dtype:
                raise ValueError("all inputs must have the same device and floating-point dtype")

    def forward(
        self,
        forecast: Tensor,
        geography: Tensor | None = None,
        baseline: Tensor | None = None,
        time_features: Tensor | None = None,
    ) -> Tensor:
        if baseline is None:
            raise ValueError("baseline is required; pass the aligned fine-grid baseline")
        self._validate_inputs(forecast, geography, baseline, time_features)
        skips = []
        if geography is None:
            # Upsample every forecast input channel, not just the output targets.
            # Baseline supplies grid size; coextensive inputs must be aligned upstream.
            features = F.interpolate(
                forecast, size=baseline.shape[-2:], mode="bilinear", align_corners=False
            )
        else:
            features = geography
        for level, encoder in enumerate(self.spatial_encoder):
            if level:
                features = self.pool(features)
            features = encoder(features)
            skips.append(features)

        coarse_features = self.forecast_encoder(forecast)
        coarse_features = F.interpolate(
            coarse_features, size=features.shape[-2:], mode="bilinear", align_corners=False
        )
        fused = [features, coarse_features]
        if time_features is not None:
            fused.append(
                time_features[:, :, None, None].expand(-1, -1, *features.shape[-2:])
            )
        features = self.fusion(torch.cat(fused, dim=1))

        for decoder, skip in zip(self.decoder, reversed(skips[:-1])):
            # Match actual skip sizes, including odd and rectangular input grids.
            features = F.interpolate(
                features, size=skip.shape[-2:], mode="bilinear", align_corners=False
            )
            features = decoder(torch.cat([features, skip], dim=1))
        correction = torch.cat([head(features) for head in self.heads], dim=1)
        return baseline + correction
