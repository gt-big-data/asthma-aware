"""Download GEOS-CF v1 forecasts for surface pollution and meteorology.

One output file per run; never replace missing forecasts with analyses. By default
inspect availability only. --download fetches complete runs; failures are recorded
in manifest.json and return a nonzero exit code. Remote files/tars use HTTP range
reads when supported; otherwise tar members are streamed one at a time without
extracting a global archive to disk. The reference supplies
only coordinates and valid timestamps; --model-version selects the forecast model.
NO2 columns are optional and are not part of the default surface-variable request.
--hours selects the forecast horizon (1–72 hours, default 72).
Downloads upload each regional run to ShreyD06/AsthmaAware by default; use
--delete-after-upload to limit local disk use, or --no-upload for local-only work.
"""
import argparse
from contextlib import contextmanager
from html.parser import HTMLParser
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import time
from urllib.parse import urljoin, unquote
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import xarray as xr

from .forecast_hub import ForecastHub, delete_uploaded_run, run_record

PORTAL = "https://portal.nccs.nasa.gov/datashare/gmao/geos-cf"
TAR_INDEX = "https://gmao.gsfc.nasa.gov/gmaoftp/geoscf/forecasts/archive/"
CONVENTION = "hourly means stamped at the center of the hour"
FIELDS = {
    "no2": ("aqc", "no2", "mol mol-1"), "o3": ("aqc", "o3", "mol mol-1"),
    "air_temperature": ("met", "t", "k"), "specific_humidity": ("met", "q", "kg kg-1"),
    "surface_pressure": ("met", "ps", "pa"), "pbl_height": ("met", "zpbl", "m"),
    "tropcol_no2": ("xgc", "tropcol_no2", "1.0e15 molec cm-2"),
    "pm25": ("aqc", "pm25_rh35", "ug m-3"), "pm10": ("aqc", "pm10_rh35", "ug m-3"),
    "co": ("aqc", "co", "mol mol-1"), "relative_humidity": ("met", "rh", "1"),
    "precipitation": ("met", "tprec", "kg m-2 s-1"),
}
DEFAULT_FIELDS = [
    "no2", "o3", "air_temperature", "specific_humidity", "surface_pressure", "pbl_height",
]

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(v for k,v in attrs if k == "href")

def listing(url):
    for attempt in range(3):
        try:
            print("Fetching listing from", url, flush=True)
            with urlopen(url, timeout=30) as response:
                parser = Links(); parser.feed(response.read().decode())
            return [urljoin(url, link) for link in parser.links]
        except Exception:
            if attempt == 2: raise
            time.sleep(2**attempt)
            print("Retrying listing from", url, flush=True)

def plan_runs(reference, hours=72, version=None):
    """Include earlier runs covering the selected valid timestamps, not just days 1–5."""
    version = version or reference.attrs["source_version"]
    hour = {"v1": 12, "v2": 9}[version]
    times = pd.DatetimeIndex(reference.time.values)
    runs = set()
    for valid in times:
        for day in pd.date_range(valid.normalize()-pd.Timedelta(days=(hours+23)//24), valid.normalize()):
            init = day + pd.Timedelta(hours=hour)
            if 0 < (valid-init)/pd.Timedelta(hours=1) < hours:
                runs.add(init)
    return version, sorted(runs)

def run_directory(version, init):
    root = f"{PORTAL}/{version}/{'forecast' if version == 'v1' else 'fcst'}/"
    return root + init.strftime("Y%Y/M%m/D%d/") + (init.strftime("H%H/") if version == "v1" else "")

def collection(version, family):
    if version == "v2": return f"{family}_tavg_1hr_glo_L1440x721_slv"
    return f"{family}_tavg_1hr_g1440x721_{'v1' if family in ('aqc', 'chm') else 'x1'}"

def inventory(version, init, fields, tar_links):
    try:
        links = listing(run_directory(version, init))
    except Exception as exc:
        # The separate v1 archive can supply a run even if its portal directory
        # has disappeared or is temporarily unreachable.
        if version != "v1" or not tar_links:
            raise
        links = []
        portal_error = str(exc)
    else:
        portal_error = None
    result = {}
    for family in sorted({FIELDS[name][0] for name in fields}):
        coll = collection(version, family)
        files = [u for u in links if coll in u and '.nc4' in u]
        if files:
            result[family] = {"kind": "files", "urls": files}
        else:
            # v1 CHM contains the same surface NO2/O3 fields as AQC. NASA's
            # older tar archive retains CHM and MET rather than AQC and XGC.
            archive_collections = [coll]
            if version == "v1" and family == "aqc":
                archive_collections.append(collection(version, "chm"))
            candidates = [u for archive_coll in archive_collections for u in tar_links
                          if unquote(u).rsplit('/', 1)[-1]
                          == f"GEOS-CF_NRT.{archive_coll}.{init:%Y%m%d}.fct.tar"]
            if version != "v1" or not candidates:
                detail = f" (portal listing failed: {portal_error})" if portal_error else ""
                raise ValueError(f"Missing archived forecast collection {coll} for {init.date()}{detail}")
            result[family] = {"kind": "tar", "urls": [candidates[0]]}
    return result

@contextmanager
def remote_file(url):
    import fsspec
    with fsspec.open(url, mode='rb', block_size=256*1024, cache_type='blockcache',
                     client_kwargs={"timeout": __import__('aiohttp').ClientTimeout(total=180)}) as stream:
        yield stream

@contextmanager
def remote_tar(url, hours=72):
    """Prefer range reads; NASA's media server may instead return the full stream."""
    request = Request(url, headers={'Range': 'bytes=512-1023'})
    with urlopen(request, timeout=180) as response:
        if response.status == 206 and response.headers.get('Content-Range', '').startswith('bytes 512-1023/'):
            response.close()
            with remote_file(url) as stream, tarfile.open(fileobj=stream, mode='r:') as archive:
                yield archive, False
        elif response.status == 200:
            size = response.headers.get('Content-Length', 'unknown')
            print(f'Archive server has no HTTP range support; streaming {url} '
                  f'(archive size {size} bytes; stops when all {hours} hours are found)', flush=True)
            with tarfile.open(fileobj=response, mode='r|') as archive:
                yield archive, True
        else:
            raise ValueError(f'Unexpected HTTP range response for {url}: {response.status}')

def valid_from_name(name, init):
    match = re.search(r"(\d{8}_\d{2})z\+(\d{8}_\d{4})z", unquote(name))
    if not match: return None
    if pd.to_datetime(match[1],format='%Y%m%d_%H') != init: return None
    return pd.to_datetime(match[2],format='%Y%m%d_%H%M')

def read_subset(stream, fields, version, lat, lon, expected_time):
    with xr.open_dataset(stream, engine='h5netcdf') as ds:
        if len(ds.time) != 1 or pd.Timestamp(ds.time.values[0]) != expected_time:
            raise ValueError('Archive file time disagrees with its filename')
        names = {name.lower(): name for name in ds.data_vars}
        out = xr.Dataset()
        for field in fields:
            _, raw, unit = FIELDS[field]
            if field == 'pm25' and version == 'v1': raw = 'pm25_rh35_gcc'
            if raw not in names: raise ValueError(f'Missing archived variable {raw}')
            a = ds[names[raw]]
            original_unit = a.attrs.get('units', '')
            aliases = {'K':'k', 'Pa':'pa', 'mol/mol':'mol mol-1', 'kg/kg':'kg kg-1'}
            if aliases.get(original_unit, original_unit) != unit:
                raise ValueError(f'{raw}: unexpected archive units {original_unit!r}, expected {unit!r}')
            for dim in list(a.dims):
                if dim not in ('time','lat','lon'):
                    if a.sizes[dim] != 1: raise ValueError(f'{raw}: unexpected vertical dimension')
                    a = a.isel({dim:0}, drop=True)
            a = a.sel(lat=lat, lon=lon).transpose('time','lat','lon').load()
            if not np.isfinite(a.values).all(): raise ValueError(f'{raw}: nonfinite forecast data')
            a.attrs = dict(units=unit, source_name=raw, source_units=original_unit)
            out[field] = a
        return out

def fetch_run(version, init, resources, fields, lat, lon, hours=72):
    expected = pd.date_range(init+pd.Timedelta(minutes=30), periods=hours, freq='h')
    parts = []
    for family, resource in resources.items():
        selected = [f for f in fields if FIELDS[f][0] == family]
        steps = {}
        if resource['kind'] == 'files':
            for url in resource['urls']:
                valid = valid_from_name(url, init)
                if valid not in expected: continue
                if valid in steps: raise ValueError(f'Duplicate archived forecast time {valid}')
                hour_started = time.monotonic()
                print(f'{init:%Y-%m-%d %H:%M}Z [{family}] reading hour {len(steps)+1}/{len(expected)} '
                      f'(valid {valid:%Y-%m-%d %H:%M}Z)', flush=True)
                with remote_file(url) as stream:
                    steps[valid] = read_subset(stream, selected, version, lat, lon, valid)
                print(f'{init:%Y-%m-%d %H:%M}Z [{family}] completed hour {len(steps)}/{len(expected)} '
                      f'in {time.monotonic()-hour_started:.1f}s', flush=True)
        else:
            with remote_tar(resource['urls'][0], hours=hours) as (tar, streaming):
                for member in tar:
                    valid = valid_from_name(member.name, init)
                    if valid not in expected or not member.isfile(): continue
                    if valid in steps: raise ValueError(f'Duplicate archived forecast time {valid}')
                    hour_started = time.monotonic()
                    print(f'{init:%Y-%m-%d %H:%M}Z [{family}] reading hour {len(steps)+1}/{len(expected)} '
                          f'(valid {valid:%Y-%m-%d %H:%M}Z)', flush=True)
                    with tar.extractfile(member) as file:
                        # h5netcdf needs random access. Buffer only this member
                        # when the remote tar is a non-seekable HTTP stream.
                        if streaming:
                            with io.BytesIO(file.read()) as buffer:
                                steps[valid] = read_subset(buffer, selected, version, lat, lon, valid)
                        else:
                            steps[valid] = read_subset(file, selected, version, lat, lon, valid)
                    print(f'{init:%Y-%m-%d %H:%M}Z [{family}] completed hour {len(steps)}/{len(expected)} '
                          f'in {time.monotonic()-hour_started:.1f}s', flush=True)
                    if len(steps) == len(expected):
                        break
        if set(steps) != set(expected):
            raise ValueError(f'{family}: missing {len(set(expected)-set(steps))} of {hours} hourly forecast intervals')
        parts.append(xr.concat([steps[t] for t in expected],dim='time'))
    ds = xr.merge(parts,join='exact')
    ds = ds.assign_coords(lead_time=('time', ds.time.values-init.to_datetime64()),
                          forecast_reference_time=init.to_datetime64())
    ds.attrs.update(product='forecast', time_convention=CONVENTION, source='NASA GEOS-CF',
                    source_version=version, initialization_time=init.isoformat()+'Z',
                    forecast_run=init.strftime('%Y%m%d_%Hz'),
                    availability_status='unknown; initialization is not publication time')
    return ds

def validate_local_run(path, version, init, fields, lat, lon, hours=72):
    with xr.open_dataset(path, engine='h5netcdf') as cached:
        times = pd.date_range(init + pd.Timedelta(minutes=30), periods=hours, freq='h').values
        if (cached.attrs.get('product') != 'forecast'
                or cached.attrs.get('source_version') != version
                or pd.Timestamp(cached.attrs.get('initialization_time')) != init.tz_localize('UTC')
                or not np.array_equal(cached.time.values, times)
                or not np.array_equal(cached.lat.values, lat)
                or not np.array_equal(cached.lon.values, lon)
                or not set(fields) <= set(cached.data_vars)):
            raise ValueError('Existing output differs from request; use another output directory')
        for field in fields:
            value = cached[field]
            if (value.dims != ('time', 'lat', 'lon') or value.attrs.get('units') != FIELDS[field][2]
                    or not np.isfinite(value.values).all()):
                raise ValueError(f'Invalid cached forecast variable {field}')


def write_manifest(path, manifest):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model-version',choices=('v1','v2'),default='v1',
                        help='Forecast version (default: v1); independent of the reference model version')
    parser.add_argument('--variables',nargs='+',choices=sorted(FIELDS),default=DEFAULT_FIELDS,
                        help='Default: surface NO2, O3, temperature, humidity, pressure and boundary-layer height')
    parser.add_argument('--max-runs',type=int)
    parser.add_argument('--hours', type=int, default=72,
                        help='Forecast horizon in hours, from 1 to 72 (default: %(default)s)')
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--hf-repo', default='ShreyD06/AsthmaAware',
                        help='Hugging Face dataset for downloaded regional runs (default: %(default)s)')
    parser.add_argument('--hf-prefix', help='Repository folder (default: geos_cf/OUTPUT_DIRECTORY_NAME)')
    parser.add_argument('--no-upload', action='store_true', help='Keep downloads local without accessing Hugging Face')
    parser.add_argument('--delete-after-upload', action='store_true',
                        help='Remove each local NetCDF and sidecar only after verifying their upload')
    args = parser.parse_args(argv)

    if args.model_version == 'v1' and 'pm10' in args.variables:
        parser.error('GEOS-CF v1 does not provide PM10')
    if args.max_runs is not None and args.max_runs < 1:
        parser.error('--max-runs must be positive')
    if not 1 <= args.hours <= 72:
        parser.error('--hours must be between 1 and 72')
    if args.delete_after_upload and (not args.download or args.no_upload):
        parser.error('--delete-after-upload requires --download with uploads enabled')
    args.output.mkdir(parents=True,exist_ok=True)

    with xr.open_dataset(args.reference, engine='h5netcdf') as ref:
        version, runs = plan_runs(ref, hours=args.hours, version=args.model_version)
        lat, lon = ref.lat.values.copy(), ref.lon.values.copy()
        valid_times = [pd.Timestamp(t).isoformat()+'Z' for t in ref.time.values]
        print("Reference and run times determined")

    selected_runs = runs[:args.max_runs] if args.max_runs else runs
    request = dict(source_version=version, variables=sorted(args.variables), valid_times=valid_times,
                   lat=lat.tolist(), lon=lon.tolist(), lead_interval_hours=[0, args.hours])
    request_id = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    manifest = dict(product='forecast',source_version=version, variables=list(args.variables),
                    reference=str(args.reference.resolve()),
                    valid_times=valid_times,lead_interval_hours=[0,args.hours], files=[],failures=[],
                    planned_runs=len(runs),attempted_runs=len(selected_runs),inventory=[],download_requested=args.download,
                    request_id=request_id, uploads={}, local_files_removed=args.delete_after_upload)
    hub = None
    if args.download and not args.no_upload:
        try:
            hub = ForecastHub(args.hf_repo, args.hf_prefix or f'geos_cf/{args.output.resolve().name}')
            previous = hub.load_manifest()
            if previous is not None:
                if previous.get('request_id') != request_id:
                    raise ValueError('The Hub folder contains a different forecast request; '
                                     'choose a different --hf-prefix')
                manifest['uploads'] = previous.get('uploads', {})
            manifest['huggingface'] = dict(repo_id=hub.repo_id, repo_type='dataset', prefix=hub.prefix)
        except Exception as exc:
            parser.error(f'Cannot initialize Hugging Face upload: {exc}')
    # Authentication and remote resume checks happen before any NASA downloads.
    tar_links = None
    if len(selected_runs) < len(runs):
        manifest['failures'].append({'reason':'Run limit requested; archive coverage is intentionally incomplete'})
    for init in selected_runs:
        path = args.output/f'geos_cf_{version}_forecast_{init:%Y%m%d_%Hz}_h000-{args.hours:03d}.nc'
        stage = 'download'
        failed = False
        try:
            record = manifest['uploads'].get(path.name)
            if hub and record and hub.verify_run(path, record):
                manifest['files'].append(path.name)
                if args.delete_after_upload:
                    delete_uploaded_run(path, record)
                print(f'{init}: already uploaded and verified', flush=True)
            else:
                manifest['uploads'].pop(path.name, None)
                if not (args.download and path.exists()):
                    if tar_links is None:
                        try: tar_links = listing(TAR_INDEX) if version == 'v1' else []
                        except Exception: tar_links = []
                    resources = inventory(version,init,args.variables,tar_links)
                    manifest['inventory'].append({'initialization_time':init.isoformat()+'Z','resources':resources})
                    if args.download:
                        ds=fetch_run(version,init,resources,args.variables,lat,lon,hours=args.hours)
                        metadata=dict(ds.attrs, hours=args.hours,valid_time_start=str(ds.time.values[0]),
                                      valid_time_end=str(ds.time.values[-1]),units={k:v.attrs['units'] for k,v in ds.data_vars.items()},
                                      source_urls=[u for item in resources.values() for u in item['urls']],
                                      available_at=None,downloaded_at=pd.Timestamp.now(tz='UTC').isoformat())
                        ds.attrs['source_urls'] = json.dumps(metadata['source_urls'])
                        ds.attrs['downloaded_at'] = metadata['downloaded_at']
                        try:
                            tmp=path.with_suffix('.nc.tmp')
                            ds.to_netcdf(tmp,engine='h5netcdf',encoding={k:{'zlib':True,'complevel':4} for k in ds.data_vars})
                            tmp.replace(path)
                            write_manifest(path.with_suffix('.metadata.json'), metadata)
                        finally:
                            ds.close()
                if args.download:
                    validate_local_run(path, version, init, args.variables, lat, lon, hours=args.hours)
                    manifest['files'].append(path.name)
                    if hub:
                        stage = 'upload'
                        record = run_record(path)
                        manifest['uploads'][path.name] = record
                        try:
                            hub.publish(manifest, (path, path.with_suffix('.metadata.json')))
                            if not hub.verify_run(path, record):
                                raise ValueError('Uploaded forecast failed remote checksum verification')
                        except Exception:
                            manifest['uploads'].pop(path.name, None)
                            raise
                        if args.delete_after_upload:
                            delete_uploaded_run(path, record)
                print(f'{init}: {"uploaded and verified" if hub else "ready" if args.download else "available"}',flush=True)
        except Exception as exc:
            failed = True
            manifest['failures'].append({'initialization_time':init.isoformat()+'Z','reason':str(exc)})
            print(f'{init}: blocked: {exc}',flush=True)
        target=args.output/('manifest.json' if args.download else 'inventory.json')
        if hub:
            manifest['huggingface']['revision'] = hub.revision
        write_manifest(target, manifest)
        if failed and stage == 'upload':
            print('Stopping after upload failure; local run files have been kept for retry.', flush=True)
            break
    if hub:
        try:
            hub.publish(manifest)
        except Exception as exc:
            manifest['failures'].append({'reason': f'Final Hub manifest update failed: {exc}'})
        manifest['huggingface']['revision'] = hub.revision
        write_manifest(args.output / 'manifest.json', manifest)
    return 1 if manifest['failures'] else 0

if __name__=='__main__':
    raise SystemExit(main())
