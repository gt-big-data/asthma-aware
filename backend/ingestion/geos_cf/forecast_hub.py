"""Publish regional forecast runs and a resumable manifest to a dataset on the Hub."""

import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile


def file_fingerprint(path):
    """Hashes used by the Hub for LFS objects and ordinary Git files."""
    size = path.stat().st_size
    sha256 = hashlib.sha256()
    git_sha1 = hashlib.sha1(f'blob {size}\0'.encode())
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha256.update(chunk)
            git_sha1.update(chunk)
    return {'size': size, 'sha256': sha256.hexdigest(), 'git_blob_sha1': git_sha1.hexdigest()}


def run_record(path):
    return {'files': {p.name: file_fingerprint(p)
                      for p in (path, path.with_suffix('.metadata.json'))}}


def delete_uploaded_run(path, record):
    """Delete only the two known output files, after checking their local hashes."""
    paths = (path, path.with_suffix('.metadata.json'))
    if set(record['files']) != {p.name for p in paths}:
        raise ValueError('Upload record does not describe this forecast run')
    for local in paths:
        if local.exists() and file_fingerprint(local) != record['files'][local.name]:
            raise ValueError(f'Local file changed since upload; keeping {local.name}')
    for local in paths:
        local.unlink(missing_ok=True)


class ForecastHub:
    def __init__(self, repo_id, prefix):
        from huggingface_hub import HfApi, get_token

        parts = prefix.split('/')
        if not prefix or '\\' in prefix or any(p in ('', '.', '..') for p in parts):
            raise ValueError('--hf-prefix must be a relative repository folder without . or ..')
        token = get_token()
        if not token:
            raise ValueError('Hugging Face uploads require HF_TOKEN or `hf auth login` '
                             'with write access. Use --no-upload for local downloads only.')
        self.repo_id = repo_id
        self.prefix = str(PurePosixPath(prefix))
        self.api = HfApi(token=token)
        # Pin reads and use optimistic locking so simultaneous writers cannot
        # replace one another's manifests, even when the repo has other files.
        self.revision = self.api.repo_info(repo_id, repo_type='dataset').sha

    def load_manifest(self):
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import EntryNotFoundError

        try:
            # Only the small manifest is cached, in temporary storage.
            with tempfile.TemporaryDirectory(prefix='geos-forecast-hub-') as cache:
                path = hf_hub_download(
                    self.repo_id, f'{self.prefix}/manifest.json', repo_type='dataset',
                    revision=self.revision, token=self.api.token, cache_dir=cache)
                return json.loads(Path(path).read_text())
        except EntryNotFoundError:
            if self.api.get_paths_info(self.repo_id, paths=[self.prefix], repo_type='dataset',
                                       revision=self.revision):
                raise ValueError('The Hub folder exists without a forecast manifest; '
                                 'choose a different --hf-prefix')
            return None

    def verify_run(self, path, record):
        expected = {path.name, path.with_suffix('.metadata.json').name}
        if not isinstance(record, dict) or set(record.get('files', {})) != expected:
            return False
        remote = self.api.get_paths_info(
            self.repo_id, paths=[f'{self.prefix}/{name}' for name in expected],
            repo_type='dataset', revision=self.revision)
        by_name = {item.path: item for item in remote}
        for name, digest in record['files'].items():
            info = by_name.get(f'{self.prefix}/{name}')
            if info is None or info.size != digest['size']:
                return False
            if info.lfs is not None:
                if info.lfs.sha256 != digest['sha256']:
                    return False
            elif info.blob_id != digest['git_blob_sha1']:
                return False
        return True

    def publish(self, manifest, paths=()):
        from huggingface_hub import CommitOperationAdd

        remote = copy.deepcopy(manifest)
        # Hub-relative filenames make snapshot downloads usable by the existing
        # ML manifest loader. Never advertise runs whose upload failed.
        remote['files'] = list(remote['uploads'])
        remote['reference'] = Path(remote['reference']).name
        remote['huggingface'].pop('revision', None)  # A commit cannot contain its own SHA.
        operations = [CommitOperationAdd(path_in_repo=f'{self.prefix}/{path.name}',
                                         path_or_fileobj=path) for path in paths]
        operations.append(CommitOperationAdd(
            path_in_repo=f'{self.prefix}/manifest.json',
            path_or_fileobj=(json.dumps(remote, indent=2) + '\n').encode()))
        result = self.api.create_commit(
            self.repo_id, repo_type='dataset', operations=operations,
            commit_message='Upload GEOS-CF forecast run' if paths else 'Update GEOS-CF forecast manifest',
            parent_commit=self.revision)
        self.revision = result.oid
