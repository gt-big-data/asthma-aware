import copy
import unittest
import test_data_pipeline as fixtures
from asthma_aware.data_pipeline import build_catalog, SourceDataset, fit_normalization, make_loader
from asthma_aware.source_adapters import source_config, TimeMatcher
from asthma_aware.train_model import initialize_training

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PipelineTests()
        self.fixture.setUp()
    def tearDown(self):
        self.fixture.tearDown()
    def test_new_source_names_need_no_dispatch_changes(self):
        cfg = copy.deepcopy(self.fixture.cfg)
        cfg['sources'] = {
            'new_station': source_config('o3', cfg['sources']['o3']),
            'new_raster': source_config('no2', cfg['sources']['no2']),
        }
        bundle = build_catalog(cfg)
        for name in cfg['sources']:
            self.assertTrue(bundle['reports'][name]['ready'])
            ds = SourceDataset(bundle, name)
            norm = fit_normalization(ds)
            ds.close()
            loader = make_loader(bundle, name, 'train', norm)
            models, _, configs = initialize_training({name: loader}, architecture={
                'encoder_channels': (4,8), 'forecast_encoder_channels': (4,), 'head_channels': 4})
            self.assertEqual(configs[name].target_names, (bundle['reports'][name]['target'],))
            self.assertEqual('rows' in loader.dataset[0], name == 'new_station')
            loader.dataset.close()
    def test_invalid_adapter_policy_combination(self):
        cfg = source_config('o3', self.fixture.cfg['sources']['o3'])
        cfg['timing_policy'] = 'weighted_pass_midpoints_within_1h'
        with self.assertRaises(ValueError): TimeMatcher(cfg)
