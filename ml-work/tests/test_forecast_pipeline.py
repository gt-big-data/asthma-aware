import copy
import unittest
import numpy as np
import pandas as pd
import torch
import xarray as xr
import test_data_pipeline as fixtures
from asthma_aware.data_pipeline import build_catalog, inspect_geos, SourceDataset, fit_normalization, make_loader
from asthma_aware.train_model import initialize_training, train_one_epoch, evaluate

class ForecastPipelineTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineTests(); self.f.setUp()
        self.cfg = copy.deepcopy(self.f.cfg)
        self.cfg['geos_files'] = []
        self.cfg['required_product'] = 'forecast'
        self.cfg['geos_valid_times'] = [t.isoformat() for t in self.f.times]
        with xr.open_dataset(self.f.nc, engine='h5netcdf') as d: template=d.isel(time=0,drop=True).load()
        for init in pd.date_range('2022-12-30',periods=12):
            times = pd.date_range(init+pd.Timedelta(minutes=30),periods=72,freq='h')
            d = template.expand_dims(time=times).copy()
            d = d.assign_coords(lead_time=('time',times.values-init.to_datetime64()), forecast_reference_time=init.to_datetime64())
            d.attrs.update(product='forecast',initialization_time=init.isoformat()+'Z',forecast_run=str(init.date()),source_version='v1')
            p=self.f.root/f'forecast_{init.date()}.nc'; d.to_netcdf(p,engine='h5netcdf')
            self.cfg['geos_files'].append(str(p))
    def tearDown(self): self.f.tearDown()
    def test_overlapping_runs_split_and_training(self):
        torch.set_num_threads(1)
        b=build_catalog(self.cfg)
        self.assertEqual(b['product'],'forecast')
        for source in b['indexes']:
            by_date={}
            for rec in b['indexes'][source]:
                by_date.setdefault(rec['observation_date'],set()).add(rec['split'])
                self.assertEqual(len({x['forecast_run'] for x in rec['geos']}),1)
                self.assertGreater(rec['lead_time_hours'],0)
                self.assertLess(rec['lead_time_hours'],72)
            self.assertTrue(all(len(v)==1 for v in by_date.values()))
            self.assertGreater(len(b['indexes'][source]),10)
        ds=SourceDataset(b,'o3'); norm=fit_normalization(ds); ds.close()
        loader=make_loader(b,'o3','train',norm,batch_size=2)
        batch=next(iter(loader));self.assertEqual(batch['time_features'].shape[1],5)
        models,opts,cfg=initialize_training({'o3':loader},architecture={'encoder_channels':(4,8),'forecast_encoder_channels':(4,),'head_channels':4})
        self.assertEqual(cfg['o3'].time_channels,5)
        self.assertGreater(train_one_epoch(models['o3'],loader,opts['o3'])['updates'],0)
        result=evaluate(models['o3'],loader,return_details=True)
        self.assertTrue(all(v is not None for v in result['horizon_mse'].values()))
        self.assertEqual(sum(result['horizon_counts'].values()),result['valid_observations'])
        loader.dataset.close()
    def test_rejects_mixed_products_and_bad_leads(self):
        with self.assertRaises(ValueError):inspect_geos([self.cfg['geos_files'][0],str(self.f.nc)])
        p=self.cfg['geos_files'][0]
        with xr.open_dataset(p,engine='h5netcdf') as d: ds=d.load()
        ds=ds.assign_coords(lead_time=('time',ds.lead_time.values+np.timedelta64(1,'h')))
        ds.to_netcdf(p,engine='h5netcdf',mode='w')
        with self.assertRaises(ValueError):inspect_geos([p])
    def test_24_hour_runs_use_only_first_day_evaluation_bucket(self):
        torch.set_num_threads(1)
        for path in self.cfg['geos_files']:
            with xr.open_dataset(path, engine='h5netcdf') as source:
                shortened = source.isel(time=slice(0,24)).load()
            shortened.to_netcdf(path, engine='h5netcdf', mode='w')
        bundle = build_catalog(self.cfg)
        self.assertTrue(bundle['reports']['o3']['ready'])
        self.assertTrue(all(0 < record['lead_time_hours'] < 24
                            for records in bundle['indexes'].values() for record in records))
        dataset = SourceDataset(bundle, 'o3')
        normalization = fit_normalization(dataset)
        dataset.close()
        loader = make_loader(bundle, 'o3', 'train', normalization, batch_size=2)
        try:
            models, _, _ = initialize_training({'o3':loader}, architecture={
                'encoder_channels':(4,8), 'forecast_encoder_channels':(4,), 'head_channels':4})
            result = evaluate(models['o3'], loader, return_details=True)
            self.assertGreater(result['horizon_counts']['0-24'], 0)
            self.assertEqual(result['horizon_counts']['24-48'], 0)
            self.assertEqual(result['horizon_counts']['48-72'], 0)
            self.assertIsNone(result['horizon_mse']['24-48'])
            self.assertIsNone(result['horizon_mse']['48-72'])
        finally:
            loader.dataset.close()
    def test_interpolation_never_borrows_another_run(self):
        # First run only left, second run only right: no station interpolation exists.
        self.cfg['sources']={'temperature':self.cfg['sources']['temperature']}
        paths=[]
        for i,hour in enumerate([11,12]):
            p=self.cfg['geos_files'][i]
            with xr.open_dataset(p,engine='h5netcdf') as d:
                ds=d.sel(time=[np.datetime64(f'2023-01-01T{hour}:30')]).load()
            ds.to_netcdf(p,engine='h5netcdf',mode='w');paths.append(p)
        self.cfg['geos_files']=paths
        b=build_catalog(self.cfg)
        self.assertEqual(b['reports']['temperature']['temporal_matches'],0)
