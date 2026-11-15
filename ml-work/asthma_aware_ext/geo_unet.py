"""The existing ResidualUNet, fed forecast and geography together at full resolution.

``asthma_aware.unet.ResidualUNet`` is used unchanged. In its own geography mode
the full-resolution encoder sees only geography, and the forecast joins at the
bottleneck. This wrapper instead passes ``[upsampled forecast, geography]`` as the
model's "geography" input, so fine-grid corrections can depend on both the place
and the day's forecast. The coarse forecast encoder still joins at the bottleneck.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from asthma_aware.unet import ResidualUNet


@dataclass(frozen=True)
class ForecastGeographyConfig:
    forecast_channels: int
    geography_channels: int
    target_names: tuple[str, ...]
    encoder_channels: tuple[int, ...] = (32, 64, 128, 256)
    forecast_encoder_channels: tuple[int, ...] = (32, 64)
    head_channels: int = 32
    time_channels: int = 0
    zero_init_residual: bool = True

    def build_model(self) -> "ForecastGeographyUNet":
        return ForecastGeographyUNet(**asdict(self))


class ForecastGeographyUNet(nn.Module):
    def __init__(self, *, forecast_channels: int, geography_channels: int, **kwargs) -> None:
        super().__init__()
        if geography_channels < 1:
            raise ValueError("Use ResidualUNet directly when there is no geography")
        self.forecast_channels = forecast_channels
        self.geography_channels = geography_channels
        self.unet = ResidualUNet(
            forecast_channels=forecast_channels,
            geography_channels=forecast_channels + geography_channels,
            **kwargs,
        )

    def forward(
        self,
        forecast: Tensor,
        geography: Tensor,
        baseline: Tensor,
        time_features: Tensor | None = None,
    ) -> Tensor:
        if geography is None or geography.shape[1] != self.geography_channels:
            raise ValueError(f"geography must have {self.geography_channels} channels")
        if forecast.dim() != 4 or baseline.dim() != 4:
            raise ValueError("forecast and baseline must be [B, C, H, W]")
        upsampled = F.interpolate(
            forecast, size=baseline.shape[-2:], mode="bilinear", align_corners=False
        )
        if geography.shape[0] == 1 and forecast.shape[0] > 1:
            geography = geography.expand(forecast.shape[0], -1, -1, -1)
        return self.unet(
            forecast,
            torch.cat([upsampled, geography], dim=1),
            baseline,
            time_features,
        )
