import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
import torch
import xarray as xr
from asthma_aware.data_pipeline import (
    Grid,
    indices,
    column_factor,
    dewpoint_c,
    station_pair,
    assign_splits,
    build_catalog,
    SourceDataset,
    fit_normalization,
    make_loader,
    masked_loss,
    TIME_CONVENTION,
)
from asthma_aware.train_model import initialize_training, train_one_epoch


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.grid = Grid(width=8, height=8, transform=(0.1, 0, -84, 0, -0.1, 34))
        dates = pd.date_range("2023-01-01", periods=10, freq="D", tz="UTC")
        self.times = [
            d + pd.Timedelta(hours=h, minutes=30) for d in dates for h in [11, 12]
        ]
        shape = (20, 4, 4)
        values = {
            "air_temperature": (290.0, "k"),
            "specific_humidity": (0.005, "kg kg-1"),
            "surface_pressure": (100000.0, "pa"),
            "pbl_height": (500.0, "m"),
            "no2": (1e-9, "mol mol-1"),
            "o3": (3e-8, "mol mol-1"),
            "tropcol_no2": (2.0, "1.0e15 molec cm-2"),
            "totalcol_o3": (0.12, "mol/m2"),
        }
        ds = xr.Dataset(
            {
                k: (
                    ("time", "lat", "lon"),
                    np.full(shape, v, dtype=np.float32),
                    {"units": u},
                )
                for k, (v, u) in values.items()
            },
            coords={
                "time": pd.DatetimeIndex(self.times).tz_localize(None),
                "lat": [33.3, 33.5, 33.7, 33.9],
                "lon": [-83.9, -83.7, -83.5, -83.3],
            },
            attrs={"product": "analysis", "time_convention": TIME_CONVENTION},
        )
        self.nc = self.root / "geos.nc"
        ds.to_netcdf(self.nc, engine="h5netcdf")
        self.labels = self.root / "labels.tif"
        self.mask = self.root / "mask.tif"
        for path, value in [(self.labels, 2.0), (self.mask, 1.0)]:
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                height=8,
                width=8,
                count=10,
                dtype="float32",
                crs="EPSG:4326",
                transform=self.grid.affine,
            ) as dst:
                for i, d in enumerate(dates, 1):
                    dst.write(
                        np.full(
                            (8, 8),
                            value + i * 0.01 if path == self.labels else value,
                            dtype=np.float32,
                        ),
                        i,
                    )
                    dst.set_band_description(i, d.strftime("%Y%m%d"))
        rows = []
        for i, d in enumerate(dates):
            row = {"station_id": "A", "target_time_utc": d + pd.Timedelta(hours=12)}
            for p in ["temperature", "dewpoint"]:
                row.update(
                    {
                        p + "_c": float(10 + i),
                        p + "_valid": True,
                        p + "_latitude": 33.75,
                        p + "_longitude": -83.75,
                    }
                )
            rows.append(row)
        self.station = self.root / "stations.parquet"
        pd.DataFrame(rows).to_parquet(self.station)
        self.cfg = {
            "geos_files": [str(self.nc)],
            "grid": self.grid.as_dict(),
            "sources": {
                "no2": {
                    "labels": str(self.labels),
                    "observation_mask": str(self.mask),
                    "label_units": "umol/m2",
                    "coarse_shape": [4, 4],
                },
                "o3": {
                    "labels": str(self.labels),
                    "observation_mask": str(self.mask),
                    "label_units": "mol/m2",
                    "baseline_quantity": "total_o3_column",
                    "coarse_shape": [4, 4],
                },
                "temperature": {"labels": str(self.station), "coarse_shape": [4, 4]},
                "dewpoint": {"labels": str(self.station), "coarse_shape": [4, 4]},
            },
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_end_to_end(self):
        torch.set_num_threads(1)
        bundle = build_catalog(self.cfg)
        loaders = {}
        for name, report in bundle["reports"].items():
            self.assertTrue(report["ready"], report)
            self.assertEqual(
                report["split_counts"], {"train": 7, "validation": 1, "test": 2}
            )
            raw = SourceDataset(bundle, name, "train")
            norm = fit_normalization(raw)
            raw.close()
            loaders[name] = make_loader(bundle, name, "train", norm, batch_size=2)
            sample = next(iter(loaders[name]))
            self.assertEqual(sample["forecast"].shape[0], 2)
            self.assertTrue(torch.isfinite(sample["baseline"]).all())
            with self.assertRaises(ValueError):
                fit_normalization(SourceDataset(bundle, name, "test"))
        models, optimizers, _ = initialize_training(
            loaders,
            architecture={
                "encoder_channels": (4, 8),
                "forecast_encoder_channels": (4,),
                "head_channels": 4,
            },
        )
        for name in models:
            result = train_one_epoch(models[name], loaders[name], optimizers[name])
            self.assertGreater(result["updates"], 0)
            loaders[name].dataset.close()
        self.assertEqual(
            bundle["indexes"]["no2"][0]["target_time"], "2023-01-01T12:30:00+00:00"
        )
        self.assertEqual(len(bundle["indexes"]["temperature"][0]["geos"]), 2)

    def test_masks_units_and_independent_sources(self):
        self.cfg["sources"]["no2"].pop("observation_mask")
        self.cfg["sources"]["o3"]["baseline_variable"] = "o3"
        b = build_catalog(self.cfg)
        self.assertFalse(b["reports"]["no2"]["ready"])
        self.assertFalse(b["reports"]["o3"]["ready"])
        self.assertTrue(b["reports"]["temperature"]["ready"])
        with self.assertRaises(ValueError):
            SourceDataset(b, "no2")
        self.assertAlmostEqual(column_factor("1.0e15 molec cm-2"), 1e19 / 6.02214076e23)
        with self.assertRaises(ValueError):
            column_factor("mol mol-1")
        self.assertAlmostEqual(
            float(dewpoint_c(np.array(293.15), np.array(0.01), np.array(100000))),
            14.03,
            delta=0.1,
        )

    def test_time_gaps_and_station_indices(self):
        self.assertIsNone(
            station_pair("2023-01-01T12:00Z", {"2023-01-01T11:30:00+00:00": {}})
        )
        r, c, v = indices(self.grid, [33.75, 34.1, np.nan], [-83.75, -83.75, -83.75])
        self.assertEqual((r[0], c[0]), (2, 2))
        self.assertEqual(v.tolist(), [True, False, False])
        records = [
            {"target_time": f"2023-01-{day:02}T{h:02}:00:00Z"}
            for day in range(1, 11)
            for h in [1, 2]
        ]
        assign_splits(records)
        self.assertEqual(records[0]["split"], records[1]["split"])
        prediction = torch.ones((1, 1, 8, 8), requires_grad=True)
        batch = {
            "targets": torch.tensor([[[2.0], [float("nan")]]]),
            "valid": torch.tensor([[[True], [False]]]),
            "rows": torch.tensor([[2, 0]]),
            "cols": torch.tensor([[2, 0]]),
        }
        loss = masked_loss(prediction, batch)
        loss.backward()
        self.assertEqual(prediction.grad.count_nonzero().item(), 1)
        batch["valid"][:] = False
        self.assertIsNone(masked_loss(prediction, batch))

    def test_mismatch_and_training_only_statistics(self):
        with rasterio.open(self.mask, "r+") as dst:
            dst.transform = from_origin(-85, 34, 0.1, 0.1)
        b = build_catalog(self.cfg)
        self.assertFalse(b["reports"]["no2"]["ready"])
        raw = SourceDataset(b, "temperature", "train")
        norm = fit_normalization(raw)
        raw.close()
        self.assertAlmostEqual(
            norm["target_mean"], 13.0
        )  # 10..16; excludes validation/test 17..19
        self.cfg["sources"]["temperature"]["grid"] = Grid(
            width=8, height=8, transform=(0.1, 0, -90, 0, -0.1, 34)
        ).as_dict()
        b = build_catalog(self.cfg)
        self.assertFalse(b["reports"]["temperature"]["ready"])

    def test_actual_dates_and_unknown_units(self):
        with rasterio.open(self.labels, "r+") as dst:
            for i in range(1, 11):
                dst.set_band_description(i, f"2025-01-{i:02}")
        self.cfg["sources"]["o3"].pop("label_units")
        b = build_catalog(self.cfg)
        self.assertEqual(b["reports"]["no2"]["temporal_matches"], 0)
        self.assertFalse(b["reports"]["o3"]["ready"])
        self.assertEqual(b["reports"]["temperature"]["temporal_matches"], 10)

    def test_masked_pixels_pickle_and_stale_data(self):
        import pickle

        with rasterio.open(self.mask, "r+") as dst:
            for band in range(1, 11):
                m = dst.read(band)
                m[0, 0] = 0
                dst.write(m, band)
        b = build_catalog(self.cfg)
        ds = SourceDataset(b, "no2", "train")
        sample = ds[0]
        self.assertFalse(sample["valid"][0, 0, 0])
        self.assertEqual(sample["targets"][0, 0, 0], 0)
        cloned = pickle.loads(pickle.dumps(ds))
        self.assertEqual(len(cloned.readers.handles), 0)
        torch.testing.assert_close(cloned[0]["forecast"], sample["forecast"])
        cloned.close()
        ds.close()
        with rasterio.open(self.mask, "r+") as dst:
            m = dst.read(1)
            m[0, 1] = 0
            dst.write(m, 1)
        with self.assertRaises(ValueError):
            SourceDataset(b, "no2")

    def test_nonfinite_geos_exclusion_and_sidecar(self):
        with xr.open_dataset(self.nc, engine="h5netcdf") as src:
            data = src.load()
        data["air_temperature"].values[0, 0, 0] = np.nan
        data.to_netcdf(self.nc, engine="h5netcdf", mode="w")
        sidecar = self.root / "acquisition.json"
        sidecar.write_text(
            json.dumps(
                [
                    {
                        "file": "labels.tif",
                        "band": 1,
                        "acquisition_time": "2023-01-01T18:23:00Z",
                    }
                ]
            )
        )
        self.cfg["sources"]["no2"]["acquisition_sidecar"] = str(sidecar)
        b = build_catalog(self.cfg)
        self.assertEqual(
            b["indexes"]["no2"][0]["target_time"], "2023-01-01T12:30:00+00:00"
        )
        self.assertEqual(
            b["indexes"]["no2"][0]["actual_acquisition_time"],
            "2023-01-01T18:23:00+00:00",
        )
        self.assertEqual(len(b["reports"]["temperature"]["excluded_samples"]), 1)
        self.assertEqual(len(b["indexes"]["temperature"]), 9)


if __name__ == "__main__":
    unittest.main()
