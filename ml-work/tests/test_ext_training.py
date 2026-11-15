"""Checks for the weekly split, scoring, and geography training (asthma_aware_ext)."""

import argparse
import datetime as dt
import math
import unittest

import numpy as np
import rasterio
import torch

from asthma_aware.data_pipeline import SourceDataset, build_catalog, fit_normalization
from asthma_aware_ext.evaluate import NormalizedView, evaluation_loader, load_checkpoint_model, score
from asthma_aware_ext.splits import apply_split, weekly_blocks, weekly_split
from asthma_aware_ext.train_geo import train_source
import test_data_pipeline as existing  # module import, so pytest does not re-collect its tests


class WeeklySplitTests(unittest.TestCase):
    def test_every_season_in_every_set_with_gaps(self):
        mapping, summary = weekly_split(year=2023, seed=7)
        self.assertEqual(len(mapping), 365)
        self.assertEqual(sum(len(b) for b in weekly_blocks(2023)), 365)
        for season, counts in summary["weeks_by_season"].items():
            self.assertEqual((counts["validation"], counts["test"]), (2, 2), season)
            self.assertGreaterEqual(counts["train"], 8, season)
        days = sorted(mapping)
        for a, b in zip(days, days[1:]):
            if "gap" not in (mapping[a], mapping[b]):
                self.assertEqual(mapping[a], mapping[b], (a, b))
        self.assertEqual(weekly_split(year=2023, seed=7)[0], mapping)  # reproducible
        self.assertNotEqual(weekly_split(year=2023, seed=8)[0], mapping)


class TrainingTests(unittest.TestCase):
    setUp = existing.PipelineTests.setUp
    tearDown = existing.PipelineTests.tearDown

    def geography(self):
        path = self.root / "geo.tif"
        with rasterio.open(path, "w", driver="GTiff", height=8, width=8, count=2, dtype="float32",
                           crs="EPSG:4326", transform=self.grid.affine) as dst:
            dst.write(np.arange(64, dtype=np.float32).reshape(8, 8), 1)
            dst.write(np.full((8, 8), 3.0, np.float32), 2)
            dst.set_band_description(1, "elevation_m")
            dst.set_band_description(2, "road_km")
        return {"path": str(path), "log1p_bands": ["road_km"]}

    def split_bundle(self):
        # Fixture covers 2023-01-01..10: days 1-6 train, 7 gap, 8 validation, 9 gap, 10 test.
        labels = ["train"] * 6 + ["gap", "validation", "gap", "test"]
        mapping = {(dt.date(2023, 1, 1) + dt.timedelta(i)).isoformat(): s for i, s in enumerate(labels)}
        return apply_split(build_catalog(self.cfg), mapping)

    def test_apply_split_drops_gaps(self):
        bundle, counts = self.split_bundle()
        self.assertEqual(counts["no2"], {"train": 6, "validation": 1, "test": 1})
        self.assertEqual(counts["temperature"], {"train": 6, "validation": 1, "test": 1})
        days = {r["target_time"][:10] for r in bundle["indexes"]["no2"]}
        self.assertNotIn("2023-01-07", days)

    def test_normalized_view_matches_existing_pipeline(self):
        bundle, _ = self.split_bundle()
        for source in ("no2", "temperature"):
            raw = SourceDataset(bundle, source, "train")
            norm = fit_normalization(raw)
            a = SourceDataset(bundle, source, "train", norm)[0]
            b = NormalizedView(SourceDataset(bundle, source, "train"), norm)[0]
            for key in ("forecast", "baseline", "targets"):
                self.assertTrue(torch.allclose(a[key], b[key], atol=1e-6), (source, key))

    def test_score_is_in_physical_units(self):
        bundle, _ = self.split_bundle()
        raw = SourceDataset(bundle, "no2", "train")
        norm = fit_normalization(raw)
        loader = torch.utils.data.DataLoader(NormalizedView(SourceDataset(bundle, "no2", "train"), norm))
        # The GEOS-CF baseline itself (model=None) must match a direct computation.
        s = score(None, loader, norm, "no2", uses_geography=False)
        errs = []
        for i in range(len(raw)):
            x = raw[i]
            errs.append(((x["baseline"] - x["targets"])[x["valid"]].double() * 1e6).numpy())
        errs = np.concatenate(errs)
        self.assertAlmostEqual(s["geos_rmse"], math.sqrt((errs ** 2).mean()), places=6)
        self.assertAlmostEqual(s["rmse"], s["geos_rmse"], places=9)
        self.assertEqual(s["units"], "umol/m2")
        self.assertGreater(s["geos_rmse"], 0.1)  # not rounded away like the existing printout

    def test_train_save_and_reload(self):
        torch.set_num_threads(1)
        bundle, counts = self.split_bundle()
        args = argparse.Namespace(seed=0, num_workers=0, samples_per_epoch=None, max_validation_samples=None,
                                  epochs=2, learning_rate=1e-3)
        geo_cfg = self.geography()
        for source in ("no2", "temperature"):
            result = train_source(bundle, source, geo_cfg, {"seed": 0}, args, self.root, "cpu")
            self.assertIn(result["best_epoch"], (1, 2))
            self.assertEqual(len(result["history"]), 2)
            self.assertTrue(math.isfinite(result["test"]["rmse"]))
            payload = torch.load(self.root / f"{source}_best.pt", weights_only=False)
            model, uses_geo = load_checkpoint_model(payload)
            self.assertTrue(uses_geo)
            loader = evaluation_loader(bundle, source, "test", payload, geo_cfg)
            again = score(model, loader, payload["normalization"], source)
            self.assertAlmostEqual(again["rmse"], result["test"]["rmse"], places=5)
            loader.dataset.close()

    def test_existing_format_checkpoint_scores(self):
        # A checkpoint from asthma_aware.train_model (no geography) can be scored too.
        from asthma_aware.source_models import SourceModelConfig
        bundle, _ = self.split_bundle()
        raw = SourceDataset(bundle, "temperature", "train")
        norm = fit_normalization(raw)
        cfg = SourceModelConfig(forecast_channels=len(raw.channels), target_names=("temperature_c",),
                                time_channels=4, encoder_channels=(4, 8), forecast_encoder_channels=(4,),
                                head_channels=4)
        from dataclasses import asdict
        payload = {"source": "temperature", "model_config": asdict(cfg),
                   "model_state": cfg.build_model().state_dict(), "normalization": norm}
        model, uses_geo = load_checkpoint_model(payload)
        self.assertFalse(uses_geo)
        loader = evaluation_loader(bundle, "temperature", "test", payload, None)
        s = score(model, loader, norm, "temperature", uses_geography=False)
        self.assertAlmostEqual(s["rmse"], s["geos_rmse"], places=6)  # zero-init: equals the baseline
        loader.dataset.close()


if __name__ == "__main__":
    unittest.main()
