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
