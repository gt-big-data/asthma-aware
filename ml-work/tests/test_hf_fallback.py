import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from asthma_aware.data_pipeline import resolve_satellite_labels


class SatelliteFallbackTests(unittest.TestCase):
    def test_local_file_never_downloads(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'labels.tif'
            p.touch()
            cfg = {'sources': {'no2': {'labels': str(p), 'huggingface': {'repo_id': 'test/data'}}}}
            download = Mock()
            with patch.dict('sys.modules', {'huggingface_hub': SimpleNamespace(hf_hub_download=download)}):
                result, errors = resolve_satellite_labels(cfg)
            download.assert_not_called()
            self.assertEqual(result, cfg)
            self.assertEqual(errors, {})

    def test_missing_file_uses_cache_without_mutating_config(self):
        cfg = {'sources': {'o3': {'labels': '/missing/o3.tif', 'huggingface': {'repo_id': 'test/data', 'filename': 'remote.tif', 'revision': 'abc'}}}}
        download = Mock(return_value='/cache/remote.tif')
        with patch.dict('sys.modules', {'huggingface_hub': SimpleNamespace(hf_hub_download=download)}):
            result, errors = resolve_satellite_labels(cfg)
        download.assert_called_once_with(repo_id='test/data', repo_type='dataset', filename='remote.tif', revision='abc')
        self.assertEqual(result['sources']['o3']['labels'], '/cache/remote.tif')
        self.assertEqual(cfg['sources']['o3']['labels'], '/missing/o3.tif')
        self.assertEqual(errors, {})

    def test_failure_is_source_specific(self):
        cfg = {'sources': {'no2': {'labels': '/missing/no2.tif', 'huggingface': {'repo_id': 'test/data'}}, 'temperature': {'labels': 'stations.parquet'}}}
        with patch.dict('sys.modules', {'huggingface_hub': SimpleNamespace(hf_hub_download=Mock(side_effect=OSError('offline')))}):
            result, errors = resolve_satellite_labels(cfg)
        self.assertEqual(set(errors), {'no2'})
        self.assertIn('offline', errors['no2'])
        self.assertEqual(result, cfg)

    def test_local_labels_still_download_missing_mask(self):
        with tempfile.TemporaryDirectory() as d:
            label = Path(d) / "labels.tif"
            label.touch()
            cfg = {"sources": {"no2": {"labels": str(label),
                "observation_mask": str(Path(d) / "mask.tif"),
                "huggingface": {"repo_id": "test/data", "mask_filename": "remote-mask.tif"}}}}
            download = Mock(return_value="/cache/mask.tif")
            with patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(hf_hub_download=download)}):
                result, errors = resolve_satellite_labels(cfg)
            download.assert_called_once_with(repo_id="test/data", repo_type="dataset",
                filename="remote-mask.tif", revision="main")
            self.assertEqual(result["sources"]["no2"]["labels"], str(label))
            self.assertEqual(result["sources"]["no2"]["observation_mask"], "/cache/mask.tif")
            self.assertFalse(errors)

    def test_timestamp_bands_preserve_noon_policy(self):
        from asthma_aware.data_pipeline import satellite_day
        import pandas as pd
        day = satellite_day("2023-01-01T18:00Z")
        self.assertEqual(day, satellite_day("20230101"))
        self.assertEqual((day + pd.Timedelta(hours=12, minutes=30)).isoformat(),
                         "2023-01-01T12:30:00+00:00")

    def test_nearest_time_tolerance_and_tie(self):
        from asthma_aware.data_pipeline import nearest_geos_time
        lookup = {"2023-01-01T17:30:00+00:00": {}, "2023-01-01T18:30:00+00:00": {}}
        self.assertEqual(nearest_geos_time("2023-01-01T18:00Z", lookup), min(lookup))
        self.assertEqual(nearest_geos_time("2023-01-01T19:30Z", lookup), max(lookup))
        self.assertIsNone(nearest_geos_time("2023-01-01T19:31Z", lookup))
        self.assertIsNone(nearest_geos_time("2023-01-06T18:00Z", lookup))

    def test_weighted_midpoints(self):
        from asthma_aware.data_pipeline import weighted_pass_time
        tags = {"n_passes": "2", "passes_utc": "17:00:00-18:00:00 UTC (orbit 1, 100 px); 19:00:00-20:00:00 UTC (orbit 2, 300 px)"}
        self.assertEqual(weighted_pass_time("2023-01-01", tags), "2023-01-01T19:00:00+00:00")
        self.assertIsNone(weighted_pass_time("2023-01-01", {"n_passes": "0"}))
        with self.assertRaises(ValueError):
            weighted_pass_time("2023-01-01", {"n_passes": "1"})
