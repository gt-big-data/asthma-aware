"""Checks for the date-range weekly split and fine-tuning (asthma_aware_ext)."""

import argparse
import datetime as dt
import json
import math
import unittest
from unittest import mock

import numpy as np
import rasterio
import torch

from asthma_aware_ext import finetune_geo
from asthma_aware_ext.evaluate import evaluation_loader, load_checkpoint_model, score
from asthma_aware_ext.splits import apply_split, weekly_split, weekly_split_range
from asthma_aware_ext.train_geo import train_source
from asthma_aware.data_pipeline import build_catalog
import test_data_pipeline as existing  # module import, so pytest does not re-collect its tests
import test_ext_training as ext_training  # module import, so its tests are not re-collected here


class WeeklySplitRangeTests(unittest.TestCase):
    def test_matches_calendar_year_split(self):
        for seed in (2023, 7):
            self.assertEqual(weekly_split_range("2023-01-01", "2024-01-01", seed=seed)[0],
                             weekly_split(year=2023, seed=seed)[0])

    def test_v2_period_covers_every_season(self):
        mapping, summary = weekly_split_range(dt.date(2025, 8, 5), dt.date(2026, 11, 1), seed=2023)
        self.assertEqual(min(mapping), "2025-08-05")
        self.assertEqual(max(mapping), "2026-10-31")
        self.assertEqual(len(mapping), 453)
        for season, counts in summary["weeks_by_season"].items():
            self.assertEqual((counts["validation"], counts["test"]), (2, 2), season)
        days = sorted(mapping)
        for a, b in zip(days, days[1:]):
            if "gap" not in (mapping[a], mapping[b]):
                self.assertEqual(mapping[a], mapping[b], (a, b))


class FinetuneTests(unittest.TestCase):
    setUp = existing.PipelineTests.setUp
    tearDown = existing.PipelineTests.tearDown
    geography = ext_training.TrainingTests.geography

    def fixture_split(self, *_args, **_kwargs):
        # Fixture covers 2023-01-01..10: days 1-6 train, 7 gap, 8 validation, 9 gap, 10 test.
        labels = ["train"] * 6 + ["gap", "validation", "gap", "test"]
        mapping = {(dt.date(2023, 1, 1) + dt.timedelta(i)).isoformat(): s for i, s in enumerate(labels)}
        return mapping, {"seed": 0, "days": {s: labels.count(s) for s in set(labels)}}

    def pretrained(self, geo_cfg):
        bundle, _ = apply_split(build_catalog(self.cfg), self.fixture_split()[0])
        args = argparse.Namespace(seed=0, num_workers=0, samples_per_epoch=None, max_validation_samples=None,
                                  epochs=1, learning_rate=1e-3)
        (self.root / "pre").mkdir()
        train_source(bundle, "no2", geo_cfg, {"seed": 0}, args, self.root / "pre", "cpu")
        return self.root / "pre" / "no2_best.pt"

    def run_finetune(self, ckpt, geo_cfg, out, **extra):
        cfg_path = self.root / "config.json"
        cfg_path.write_text(json.dumps(self.cfg))
        geo_path = self.root / "geo.json"
        geo_path.write_text(json.dumps(geo_cfg))
        argv = ["--checkpoint", str(ckpt), "--config", str(cfg_path), "--geography-config", str(geo_path),
                "--start", "2023-01-01", "--end", "2023-01-11", "--epochs", "2", "--seed", "0", "--output", str(out)]
        for k, v in extra.items():
            argv += [f"--{k.replace('_', '-')}"] + ([] if v is True else [str(v)])
        with mock.patch.object(finetune_geo, "weekly_split_range", self.fixture_split):
            return finetune_geo.main(argv)

    def test_finetune_starts_from_checkpoint_and_reloads(self):
        torch.set_num_threads(1)
        geo_cfg = self.geography()
        ckpt = self.pretrained(geo_cfg)
        payload = torch.load(ckpt, weights_only=False)
        out = self.root / "ft"
        res = self.run_finetune(ckpt, geo_cfg, out)
        self.assertEqual(res["mode"], "finetune")
        self.assertAlmostEqual(res["args"]["learning_rate"], 1e-4)
        # The "before fine-tuning" score is the pretrained model on the new test days.
        bundle, _ = apply_split(build_catalog(self.cfg), self.fixture_split()[0])
        model, _ = load_checkpoint_model(payload)
        loader = evaluation_loader(bundle, "no2", "test", payload, geo_cfg)
        self.assertAlmostEqual(res["start_test"]["rmse"], score(model, loader, payload["normalization"], "no2")["rmse"], places=6)
        loader.dataset.close()
        # Fine-tuning keeps the checkpoint's normalization; the saved best model reproduces the test score.
        best = torch.load(out / "no2_best.pt", weights_only=False)
        self.assertEqual(best["normalization"], payload["normalization"])
        self.assertIn(best["epoch"], (0, 1, 2))
        model, uses_geo = load_checkpoint_model(best)
        self.assertTrue(uses_geo)
        loader = evaluation_loader(bundle, "no2", "test", best, geo_cfg)
        self.assertAlmostEqual(score(model, loader, best["normalization"], "no2")["rmse"], res["test"]["rmse"], places=5)
        loader.dataset.close()
        self.assertTrue(math.isfinite(res["test"]["rmse"]))
        self.assertEqual(json.loads((out / "metrics.json").read_text())["best_epoch"], res["best_epoch"])

    def test_from_scratch_refits_normalization(self):
        torch.set_num_threads(1)
        geo_cfg = self.geography()
        ckpt = self.pretrained(geo_cfg)
        res = self.run_finetune(ckpt, geo_cfg, self.root / "scratch", from_scratch=True)
        self.assertEqual(res["mode"], "from_scratch")
        self.assertNotIn("start_test", res)
        self.assertAlmostEqual(res["args"]["learning_rate"], 1e-3)
        self.assertTrue(math.isfinite(res["test"]["rmse"]))

    def test_rejects_a_different_geography_raster(self):
        geo_cfg = self.geography()
        ckpt = self.pretrained(geo_cfg)
        payload = torch.load(ckpt, weights_only=False)
        with rasterio.open(geo_cfg["path"], "r+") as dst:
            dst.write(np.full((8, 8), 9.0, np.float32), 1)
        with self.assertRaises(ValueError):
            finetune_geo.checkpoint_geography_normalization(payload, geo_cfg, self.grid)


if __name__ == "__main__":
    unittest.main()
