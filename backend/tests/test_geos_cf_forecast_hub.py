import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import xarray as xr
import huggingface_hub
from huggingface_hub.errors import EntryNotFoundError

from ingestion.geos_cf import fetch_forecast_history as fh
from ingestion.geos_cf.forecast_hub import delete_uploaded_run, file_fingerprint


INIT = pd.Timestamp('2023-01-01T12')
NAME = 'geos_cf_v1_forecast_20230101_12z_h000-072.nc'
PREFIX = 'geos_cf/forecasts_2023'


class FakeApi:
    def __init__(self):
        self.token = 'unit-test-token'
        self.head = 'revision-0'
        self.snapshots = {self.head: {}}
        self.commits = []
        self.fail_upload = False
        self.corrupt_upload = None

    def repo_info(self, repo_id, repo_type):
        assert repo_id == 'ShreyD06/AsthmaAware' and repo_type == 'dataset'
        return SimpleNamespace(sha=self.head)

    def create_commit(self, repo_id, *, repo_type, operations, commit_message, parent_commit):
        assert repo_id == 'ShreyD06/AsthmaAware' and repo_type == 'dataset'
        assert parent_commit == self.head
        if self.fail_upload and any(op.path_in_repo.endswith('.nc') for op in operations):
            raise OSError('Hub upload interrupted')
        files = dict(self.snapshots[self.head])
        for operation in operations:
            content = operation.path_or_fileobj
            if not isinstance(content, bytes):
                content = Path(content).read_bytes()
            if self.corrupt_upload and operation.path_in_repo.endswith(self.corrupt_upload):
                content = content[:-1] + bytes([content[-1] ^ 1])  # Same size, wrong checksum.
            files[operation.path_in_repo] = content
        self.head = f'revision-{len(self.commits) + 1}'
        self.snapshots[self.head] = files
        self.commits.append([op.path_in_repo for op in operations])
        return SimpleNamespace(oid=self.head)

    def get_paths_info(self, repo_id, *, paths, repo_type, revision):
        assert repo_id == 'ShreyD06/AsthmaAware' and repo_type == 'dataset'
        result = []
        for path in paths:
            content = self.snapshots[revision].get(path)
            if content is None:
                continue
            result.append(SimpleNamespace(
                path=path, size=len(content),
                lfs=SimpleNamespace(sha256=hashlib.sha256(content).hexdigest()) if path.endswith('.nc') else None,
                blob_id=hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()))
        return result


@pytest.fixture
def setup_fetch(tmp_path, monkeypatch):
    reference = tmp_path / 'reference.nc'
    xr.Dataset(coords={'time': [INIT + pd.Timedelta(minutes=30)],
                       'lat': [33.75], 'lon': [-84.5]}).to_netcdf(reference, engine='h5netcdf')
    output = tmp_path / 'forecasts_2023'
    calls = []
    monkeypatch.setattr(fh, 'plan_runs', lambda ref, version, hours: ('v1', [INIT]))
    monkeypatch.setattr(fh, 'listing', lambda url: calls.append('listing') or [])
    monkeypatch.setattr(fh, 'inventory', lambda *args: {
        'aqc': {'kind': 'files', 'urls': ['https://nasa.example.test/forecast.nc4']}})

    def fetch(version, init, resources, fields, lat, lon, hours=72):
        calls.append('fetch')
        times = pd.date_range(init + pd.Timedelta(minutes=30), periods=hours, freq='h')
        return xr.Dataset(
            {field: xr.DataArray(np.ones((hours, 1, 1), dtype=np.float32),
                                 dims=('time', 'lat', 'lon'), attrs={'units': fh.FIELDS[field][2]})
             for field in fields},
            coords={'time': times, 'lat': lat, 'lon': lon,
                    'forecast_reference_time': init.to_datetime64(),
                    'lead_time': ('time', times.values - init.to_datetime64())},
            attrs={'source_version': version, 'product': 'forecast',
                   'initialization_time': init.isoformat() + 'Z'})

    monkeypatch.setattr(fh, 'fetch_run', fetch)
    api = FakeApi()
    monkeypatch.setattr(huggingface_hub, 'get_token', lambda: api.token)
    monkeypatch.setattr(huggingface_hub, 'HfApi', lambda token: api)

    def download(repo_id, filename, *, repo_type, revision, token, cache_dir):
        content = api.snapshots[revision].get(filename)
        if content is None:
            raise EntryNotFoundError('No forecast manifest yet')
        path = Path(cache_dir) / 'manifest.json'
        path.write_bytes(content)
        return str(path)

    monkeypatch.setattr(huggingface_hub, 'hf_hub_download', download)
    args = ['--reference', str(reference), '--output', str(output), '--download']
    return args, output, calls, api


def test_upload_then_cleanup_and_remote_resume_without_nasa(setup_fetch):
    args, output, calls, api = setup_fetch
    assert fh.main(args + ['--delete-after-upload']) == 0
    assert not (output / NAME).exists()
    assert not (output / Path(NAME).with_suffix('.metadata.json')).exists()
    local = json.loads((output / 'manifest.json').read_text())
    assert local['files'] == [NAME] and local['failures'] == []
    assert local['huggingface']['repo_id'] == 'ShreyD06/AsthmaAware'
    assert api.commits[0] == [f'{PREFIX}/{NAME}', f'{PREFIX}/{Path(NAME).with_suffix(".metadata.json")}',
                              f'{PREFIX}/manifest.json']
    remote = json.loads(api.snapshots[api.head][f'{PREFIX}/manifest.json'])
    assert remote['reference'] == 'reference.nc'  # No machine-specific absolute path.
    assert remote['files'] == [NAME] and NAME in remote['uploads']
    calls.clear()
    assert fh.main(args + ['--delete-after-upload']) == 0
    assert calls == []  # Neither a tar listing nor a forecast redownload.
    assert all(len(commit) == 1 for commit in api.commits[1:])


def test_upload_failure_keeps_local_run_and_stops_further_fetching(setup_fetch, monkeypatch):
    args, output, calls, api = setup_fetch
    api.fail_upload = True
    monkeypatch.setattr(fh, 'plan_runs', lambda ref, version, hours: ('v1', [INIT, INIT + pd.Timedelta(days=1)]))
    assert fh.main(args + ['--delete-after-upload']) == 1
    assert (output / NAME).exists()
    assert (output / Path(NAME).with_suffix('.metadata.json')).exists()
    assert calls.count('fetch') == 1
    local = json.loads((output / 'manifest.json').read_text())
    assert local['uploads'] == {}
    assert 'Hub upload interrupted' in local['failures'][0]['reason']


@pytest.mark.parametrize('suffix', ['.nc', '.metadata.json'])
def test_failed_checksum_preserves_files(setup_fetch, suffix):
    args, output, calls, api = setup_fetch
    api.corrupt_upload = suffix
    assert fh.main(args + ['--delete-after-upload']) == 1
    assert (output / NAME).exists()
    assert (output / Path(NAME).with_suffix('.metadata.json')).exists()
    local = json.loads((output / 'manifest.json').read_text())
    assert local['uploads'] == {}
    assert 'checksum verification' in local['failures'][0]['reason']
    remote = json.loads(api.snapshots[api.head][f'{PREFIX}/manifest.json'])
    assert remote['files'] == [] and remote['failures']


def test_deleted_remote_file_is_fetched_again_instead_of_trusting_manifest(setup_fetch):
    args, output, calls, api = setup_fetch
    assert fh.main(args + ['--delete-after-upload']) == 0
    del api.snapshots[api.head][f'{PREFIX}/{NAME}']
    calls.clear()
    assert fh.main(args + ['--delete-after-upload']) == 0
    assert calls.count('fetch') == 1
    assert f'{PREFIX}/{NAME}' in api.snapshots[api.head]


def test_retry_upload_uses_existing_validated_file(setup_fetch):
    args, output, calls, api = setup_fetch
    api.fail_upload = True
    assert fh.main(args) == 1
    calls.clear()
    api.fail_upload = False
    assert fh.main(args) == 0
    assert calls == []
    assert (output / NAME).exists()  # Keeping local files is the default.


def test_missing_token_fails_before_nasa_download(setup_fetch):
    args, output, calls, api = setup_fetch
    api.token = None
    with pytest.raises(SystemExit) as exc:
        fh.main(args)
    assert exc.value.code == 2
    assert calls == []


def test_local_only_does_not_need_hub_credentials(setup_fetch):
    args, output, calls, api = setup_fetch
    api.token = None
    assert fh.main(args + ['--no-upload']) == 0
    assert (output / NAME).exists()
    assert api.commits == []


@pytest.mark.parametrize('change', [['--variables', 'no2'], ['--hours', '24']])
def test_different_request_cannot_overwrite_same_hub_folder(setup_fetch, change):
    args, output, calls, api = setup_fetch
    assert fh.main(args) == 0
    calls.clear()
    with pytest.raises(SystemExit) as exc:
        fh.main(args + change)
    assert exc.value.code == 2
    assert calls == []


def test_24_hour_upload_records_matching_data_metadata_and_manifest(setup_fetch):
    args, output, calls, api = setup_fetch
    prefix = 'geos_cf/forecasts_2023_24h'
    args += ['--hours', '24', '--hf-prefix', prefix]
    assert fh.main(args) == 0
    name = NAME.replace('h000-072', 'h000-024')
    path = output / name
    with xr.open_dataset(path, engine='h5netcdf') as ds:
        assert ds.sizes['time'] == 24
        assert ds.time.values[-1] == (INIT + pd.Timedelta(hours=23.5)).to_datetime64()
    metadata = json.loads(path.with_suffix('.metadata.json').read_text())
    assert metadata['hours'] == 24
    remote = json.loads(api.snapshots[api.head][f'{prefix}/manifest.json'])
    assert remote['lead_interval_hours'] == [0, 24]
    assert remote['files'] == [name]
    assert name in remote['uploads']
    with pytest.raises(ValueError, match='differs from request'):
        fh.validate_local_run(path, 'v1', INIT, fh.DEFAULT_FIELDS, [33.75], [-84.5], hours=72)
    calls.clear()
    assert fh.main(args + ['--delete-after-upload']) == 0
    assert not path.exists() and calls == []


@pytest.mark.parametrize('extra', [['--no-upload'], []])
def test_cleanup_requires_download_and_uploads(tmp_path, extra):
    args = ['--reference', 'unused.nc', '--output', str(tmp_path), '--delete-after-upload'] + extra
    with pytest.raises(SystemExit) as exc:
        fh.main(args)
    assert exc.value.code == 2


def test_cleanup_checks_all_files_before_deleting_any(tmp_path):
    path = tmp_path / NAME
    sidecar = path.with_suffix('.metadata.json')
    path.write_bytes(b'forecast')
    sidecar.write_bytes(b'metadata')
    record = {'files': {p.name: file_fingerprint(p) for p in (path, sidecar)}}
    sidecar.write_bytes(b'changed locally')
    with pytest.raises(ValueError, match='changed since upload'):
        delete_uploaded_run(path, record)
    assert path.exists() and sidecar.exists()
