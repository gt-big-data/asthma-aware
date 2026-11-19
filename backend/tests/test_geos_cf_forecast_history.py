import unittest
from unittest.mock import patch
from contextlib import contextmanager
import io
import json
import tarfile

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from ingestion.geos_cf import fetch_forecast_history as fh
from ingestion.geos_cf.fetch_forecast_history import plan_runs, valid_from_name, inventory, collection

class HistoryTests(unittest.TestCase):
    def test_prior_runs_and_versions(self):
        ref=xr.Dataset(coords={'time':pd.to_datetime(['2023-01-01T00:30'])},attrs={'source_version':'v1'})
        version,runs=plan_runs(ref)
        self.assertEqual(version,'v1')
        self.assertEqual([str(t) for t in runs],['2022-12-29 12:00:00','2022-12-30 12:00:00','2022-12-31 12:00:00'])
        ref.attrs['source_version']='v2'
        self.assertTrue(all(t.hour==9 for t in plan_runs(ref)[1]))
    def test_filename_run_identity(self):
        name='GEOS-CF.v01.fcst.aqc_tavg_1hr_g1440x721_v1.20230101_12z%2B20230101_1230z.nc4'
        self.assertEqual(valid_from_name(name,pd.Timestamp('2023-01-01T12')),pd.Timestamp('2023-01-01T12:30'))
        self.assertIsNone(valid_from_name(name,pd.Timestamp('2023-01-02T12')))
    def test_missing_collection_fails_before_download(self):
        with patch('ingestion.geos_cf.fetch_forecast_history.listing',return_value=[]):
            with self.assertRaisesRegex(ValueError,'Missing archived forecast collection'):
                inventory('v1',pd.Timestamp('2023-01-01T12'),['air_temperature'],[])


INIT = pd.Timestamp('2023-01-01T12')


def test_shorter_horizon_does_not_plan_older_runs():
    ref = xr.Dataset(coords={'time': pd.to_datetime(['2023-01-01T00:30'])},
                     attrs={'source_version': 'v1'})
    assert plan_runs(ref, hours=24)[1] == [pd.Timestamp('2022-12-31T12')]
    assert plan_runs(ref, hours=72)[1] == list(pd.date_range('2022-12-29T12', periods=3, freq='D'))


@pytest.mark.parametrize('hours', ['0', '-1', '73'])
def test_invalid_horizon_fails_before_opening_reference(tmp_path, hours):
    with pytest.raises(SystemExit) as exc:
        fh.main(['--reference', 'unused.nc', '--output', str(tmp_path / 'output'), '--hours', hours])
    assert exc.value.code == 2
    assert not (tmp_path / 'output').exists()


def forecast_url(family, valid, init=INIT):
    return (f'https://example.test/GEOS-CF.v01.fcst.{collection("v1", family)}.'
            f'{init:%Y%m%d_%H}z%2B{valid:%Y%m%d_%H%M}z.nc4')


def archive_url(family, day='20230101'):
    return f'https://example.test/GEOS-CF_NRT.{collection("v1", family)}.{day}.fct.tar'


def test_v1_surface_inventory_needs_aqc_and_met_only(monkeypatch):
    aqc = forecast_url('aqc', INIT + pd.Timedelta(minutes=30))
    monkeypatch.setattr(fh, 'listing', lambda url: [aqc])
    resources = inventory('v1', INIT, fh.DEFAULT_FIELDS, [archive_url('met')])
    assert resources == {
        'aqc': {'kind': 'files', 'urls': [aqc]},
        'met': {'kind': 'tar', 'urls': [archive_url('met')]},
    }
    assert 'tropcol_no2' not in fh.DEFAULT_FIELDS
    assert set(fh.DEFAULT_FIELDS) == {
        'no2', 'o3', 'air_temperature', 'specific_humidity', 'surface_pressure', 'pbl_height',
    }


@pytest.mark.parametrize('portal_down', [False, True])
def test_v1_can_use_chm_and_met_tars_without_portal(monkeypatch, portal_down):
    def listing(url):
        if portal_down:
            raise OSError('portal unavailable')
        return []

    monkeypatch.setattr(fh, 'listing', listing)
    tars = [archive_url('chm'), archive_url('met'), archive_url('met', '20230102')]
    resources = inventory('v1', INIT, fh.DEFAULT_FIELDS, tars)
    assert resources['aqc'] == {'kind': 'tar', 'urls': [archive_url('chm')]}
    assert resources['met'] == {'kind': 'tar', 'urls': [archive_url('met')]}
    assert collection('v1', 'chm') == 'chm_tavg_1hr_g1440x721_v1'


def test_column_request_still_fails_when_xgc_is_absent(monkeypatch):
    monkeypatch.setattr(fh, 'listing', lambda url: [])
    with pytest.raises(ValueError, match='Missing archived forecast collection xgc'):
        inventory('v1', INIT, ['tropcol_no2'], [archive_url('chm'), archive_url('met')])


def test_v2_does_not_reuse_v1_archive(monkeypatch):
    monkeypatch.setattr(fh, 'listing', lambda url: [])
    with pytest.raises(ValueError, match='Missing archived forecast collection'):
        inventory('v2', INIT, ['no2'], [archive_url('chm')])


@pytest.mark.parametrize('version,hour', [('v1', 12), ('v2', 9)])
def test_cli_selects_model_independently_of_reference(tmp_path, monkeypatch, version, hour):
    reference = tmp_path / 'reference.nc'
    xr.Dataset(coords={'time': pd.to_datetime(['2023-01-01T00:30']),
                       'lat': [33.75], 'lon': [-84.5]},
               attrs={'source_version': 'v2' if version == 'v1' else 'v1'}).to_netcdf(
                   reference, engine='h5netcdf')
    monkeypatch.setattr(fh, 'listing', lambda url: [])
    calls = []

    def inventory(version, init, fields, tar_links):
        calls.append((version, init, fields))
        return {}

    monkeypatch.setattr(fh, 'inventory', inventory)
    args = ['--reference', str(reference), '--output', str(tmp_path / 'output'), '--max-runs', '1']
    if version == 'v2':
        args += ['--model-version', 'v2']
    assert fh.main(args) == 1  # Explicitly bounded inventory is incomplete.
    manifest = json.loads((tmp_path / 'output' / 'inventory.json').read_text())
    assert manifest['source_version'] == version
    assert manifest['variables'] == fh.DEFAULT_FIELDS
    assert calls[0][0] == version and calls[0][1].hour == hour


@pytest.mark.parametrize('hours', [24, 72])
def test_surface_forecast_reads_requested_hours_from_aqc_files_and_met_tar(tmp_path, monkeypatch, hours):
    times = pd.date_range(INIT + pd.Timedelta(minutes=30), periods=72, freq='h')
    met_tar = tmp_path / 'met.tar'
    urls = {}
    with tarfile.open(met_tar, 'w') as archive:
        for index, valid in enumerate(times):
            for family in ('aqc', 'met'):
                variables = {}
                for field in fh.DEFAULT_FIELDS:
                    source_family, raw, unit = fh.FIELDS[field]
                    if source_family == family:
                        variables[raw.upper()] = xr.DataArray(
                            np.full((1, 1, 2, 2), index + 1.0),
                            dims=('time', 'lev', 'lat', 'lon'), attrs={'units': unit})
                ds = xr.Dataset(variables, coords={'time': [valid], 'lev': [72],
                                                   'lat': [33.5, 33.75], 'lon': [-84.5, -84.25]})
                path = tmp_path / f'{family}_{index}.nc4'
                ds.to_netcdf(path, engine='h5netcdf')
                url = forecast_url(family, valid)
                if family == 'met':
                    archive.add(path, arcname=url.rsplit('/', 1)[-1])
                else:
                    urls[url] = path

    @contextmanager
    def remote_file(url):
        with (met_tar if url == 'met.tar' else urls[url]).open('rb') as stream:
            yield stream

    monkeypatch.setattr(fh, 'remote_file', remote_file)

    class Response(io.BytesIO):
        status = 200
        headers = {}

        def read(self, size=-1):
            data = super().read(size)
            self.bytes_read = getattr(self, 'bytes_read', 0) + len(data)
            return data

    responses = []

    def response_for(request, timeout):
        response = Response(met_tar.read_bytes())
        responses.append(response)
        return response

    monkeypatch.setattr(fh, 'urlopen', response_for)
    resources = {'aqc': {'kind': 'files', 'urls': list(urls)},
                 'met': {'kind': 'tar', 'urls': ['https://example.test/met.tar']}}
    ds = fh.fetch_run('v1', INIT, resources, fh.DEFAULT_FIELDS, [33.75], [-84.5], hours=hours)
    assert dict(ds.sizes) == {'time': hours, 'lat': 1, 'lon': 1}
    assert set(ds.data_vars) == set(fh.DEFAULT_FIELDS)
    assert ds.attrs['source_version'] == 'v1' and ds.attrs['product'] == 'forecast'
    assert ds.forecast_reference_time.values == INIT.to_datetime64()
    np.testing.assert_array_equal(ds.no2.values[:, 0, 0], np.arange(1, hours + 1))
    np.testing.assert_array_equal(ds.lead_time.values / np.timedelta64(1, 'h'), np.arange(hours) + 0.5)
    assert ds.no2.attrs['units'] == 'mol mol-1'
    assert ds.pbl_height.attrs['units'] == 'm'
    if hours == 24:
        assert responses[0].bytes_read < met_tar.stat().st_size / 2
    resources['aqc']['urls'].remove(forecast_url('aqc', times[hours - 1]))
    with pytest.raises(ValueError, match=f'missing 1 of {hours}'):
        fh.fetch_run('v1', INIT, resources, fh.DEFAULT_FIELDS, [33.75], [-84.5], hours=hours)


def test_tar_range_support_keeps_subset_reads(monkeypatch):
    response = io.BytesIO()
    response.status = 206
    response.headers = {'Content-Range': 'bytes 512-1023/10240'}
    monkeypatch.setattr(fh, 'urlopen', lambda request, timeout: response)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w'):
        pass
    stream.seek(0)

    @contextmanager
    def remote_file(url):
        assert url == 'https://example.test/met.tar'
        yield stream

    monkeypatch.setattr(fh, 'remote_file', remote_file)
    with fh.remote_tar('https://example.test/met.tar') as (archive, streaming):
        assert not streaming
        assert list(archive) == []
