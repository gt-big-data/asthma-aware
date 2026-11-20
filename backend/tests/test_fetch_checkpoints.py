import hashlib
import json
from unittest.mock import Mock

import pytest

from scripts.fetch_checkpoints import download


def setup_manifest(tmp_path, monkeypatch):
    content = b"test checkpoint bytes"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"checkpoints": [{"file": "o3_best.pt", "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest()}]}))
    monkeypatch.setenv("MODEL_MANIFEST_PATH", str(path))
    return content


def test_checkpoint_download_is_pinned_and_cached(tmp_path, monkeypatch):
    content = setup_manifest(tmp_path, monkeypatch)
    bucket = Mock()
    bucket.blob.return_value.download_to_filename.side_effect = lambda path, **kw: path.write_bytes(content)
    directory = tmp_path / "models"
    download(bucket, "models/release-1", directory, ["o3"])
    download(bucket, "models/release-1", directory, ["o3"])
    bucket.blob.assert_called_once_with("models/release-1/o3_best.pt")
    assert (directory / "o3_best.pt").read_bytes() == content


def test_changed_checkpoint_cannot_replace_local_file(tmp_path, monkeypatch):
    setup_manifest(tmp_path, monkeypatch)
    directory = tmp_path / "models"
    directory.mkdir()
    previous = directory / "o3_best.pt"
    previous.write_bytes(b"previous version")
    bucket = Mock()
    bucket.blob.return_value.download_to_filename.side_effect = lambda path, **kw: path.write_bytes(b"wrong weights")
    with pytest.raises(ValueError, match="mismatch"):
        download(bucket, "models/release-1", directory)
    assert previous.read_bytes() == b"previous version"
    assert not list(directory.glob(".*.tmp"))


def test_surface_no2_manifest_uses_logical_source_name(tmp_path, monkeypatch):
    content = b"surface-no2-checkpoint"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"checkpoints": [{"file": "no2_best_surface.pt", "source": "no2",
        "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}]}))
    monkeypatch.setenv("MODEL_MANIFEST_PATH", str(path))
    bucket = Mock()
    bucket.blob.return_value.download_to_filename.side_effect = lambda path, **kw: path.write_bytes(content)
    download(bucket, "models/surface-v1", tmp_path / "models", ["no2"])
    bucket.blob.assert_called_once_with("models/surface-v1/no2_best_surface.pt")
