"""Checks for asthma_aware_ext (geography); the existing package is used unchanged."""

import os
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin
import torch

from asthma_aware import ResidualUNet
from asthma_aware.data_pipeline import (
    SourceDataset,
    build_catalog,
    fit_normalization,
    masked_loss,
)
from asthma_aware_ext import (
    ForecastGeographyConfig,
    GeographyDataset,
    fit_geography_normalization,
    make_geography_loader,
    read_geography,
)
import test_data_pipeline as existing  # module import, so pytest does not re-collect its tests


class GeographyTests(unittest.TestCase):
    # Reuse the existing synthetic GEOS, satellite, and station fixtures.
    setUp = existing.PipelineTests.setUp
    tearDown = existing.PipelineTests.tearDown

    def write_geography(self, name, transform=None, names=("elevation_m", "road_km")):
        path = self.root / name
        values = [np.arange(64, dtype=np.float32).reshape(8, 8), np.full((8, 8), 3.0, np.float32)]
        with rasterio.open(
            path, "w", driver="GTiff", height=8, width=8, count=len(names),
            dtype="float32", crs="EPSG:4326", transform=transform or self.grid.affine,
        ) as dst:
            for i, band in enumerate(names, 1):
                dst.write(values[i - 1], i)
                if band:
                    dst.set_band_description(i, band)
        return str(path)

    def test_end_to_end_every_source(self):
        torch.set_num_threads(1)
        geo_cfg = {"path": self.write_geography("geo.tif"), "log1p_bands": ["road_km"]}
        data, names = read_geography(geo_cfg["path"], self.grid, geo_cfg["log1p_bands"])
        self.assertEqual(names, ["elevation_m", "road_km"])
        self.assertTrue(np.allclose(data[1], np.log1p(3.0)))
        geo_norm = fit_geography_normalization(data, names, geo_cfg["path"])
        self.assertEqual(geo_norm["std"][1], 1.0)  # constant band is not divided by zero
        bundle = build_catalog(self.cfg)
        for source in bundle["indexes"]:
            raw = SourceDataset(bundle, source, "train")
            norm = fit_normalization(raw)
            raw.close()
            loader = make_geography_loader(bundle, source, "train", norm, geo_cfg, geo_norm, batch_size=2)
            batch = next(iter(loader))
            self.assertEqual(tuple(batch["geography"].shape), (2, 2, 8, 8))
            self.assertAlmostEqual(float(batch["geography"][:, 0].mean()), 0.0, places=5)
            model = ForecastGeographyConfig(
                forecast_channels=len(loader.dataset.channels),
                geography_channels=loader.dataset.geography_channels,
                target_names=("t",), encoder_channels=(4, 8),
                forecast_encoder_channels=(4,), head_channels=4, time_channels=4,
            ).build_model()
            optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
            updates = 0
            for batch in loader:
                prediction = model(batch["forecast"], batch["geography"], batch["baseline"], batch["time_features"])
                loss = masked_loss(prediction, batch)
                if loss is None:
                    continue
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                updates += 1
            self.assertGreater(updates, 0, source)
            loader.dataset.close()

    def test_wrapper_matches_existing_unet_with_forecast_concatenated(self):
        torch.manual_seed(0)
        model = ForecastGeographyConfig(
            forecast_channels=3, geography_channels=2, target_names=("a",),
            encoder_channels=(4, 8), forecast_encoder_channels=(4,), head_channels=4,
            zero_init_residual=False,
        ).build_model()
        self.assertIsInstance(model.unet, ResidualUNet)
        self.assertEqual(model.unet.spatial_encoder[0][0].in_channels, 3 + 2)
        forecast, geography, baseline = torch.randn(2, 3, 4, 4), torch.randn(2, 2, 16, 16), torch.randn(2, 1, 16, 16)
        upsampled = torch.nn.functional.interpolate(forecast, size=(16, 16), mode="bilinear", align_corners=False)
        expected = model.unet(forecast, torch.cat([upsampled, geography], 1), baseline)
        self.assertTrue(torch.equal(model(forecast, geography, baseline), expected))
        with self.assertRaisesRegex(ValueError, "2 channels"):
            model(forecast, geography[:, :1], baseline)

    def test_grid_names_and_staleness_are_checked(self):
        shifted = self.write_geography("shifted.tif", transform=from_origin(-84.05, 34, 0.1, 0.1))
        with self.assertRaisesRegex(ValueError, "transform does not match"):
            read_geography(shifted, self.grid)
        unnamed = self.write_geography("unnamed.tif", names=("elevation_m", ""))
        with self.assertRaisesRegex(ValueError, "needs a description"):
            read_geography(unnamed, self.grid)
        good = self.write_geography("good.tif")
        with self.assertRaisesRegex(ValueError, "unknown bands"):
            read_geography(good, self.grid, ["pop_per_km2"])
        geo_cfg = {"path": good, "log1p_bands": []}
        geo_norm = fit_geography_normalization(*read_geography(good, self.grid), good)
        bundle = build_catalog(self.cfg)
        base = SourceDataset(bundle, "no2", "train")
        GeographyDataset(base, geo_cfg, geo_norm)  # fresh fit is accepted
        os.utime(good, ns=(1, 1))
        with self.assertRaisesRegex(ValueError, "changed since normalization"):
            GeographyDataset(base, geo_cfg, geo_norm)
        base.close()


if __name__ == "__main__":
    unittest.main()
