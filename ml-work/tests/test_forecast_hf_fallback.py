import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from asthma_aware.data_pipeline import load_config, resolve_forecast_manifest


class ForecastFallbackTests(unittest.TestCase):
    revision = "a" * 40
    prefix = "geos_cf/forecasts"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.local = self.root / "local" / "manifest.json"
        self.local.parent.mkdir()
        self.snapshot = self.root / "cache" / "snapshots" / self.revision / self.prefix
        self.snapshot.mkdir(parents=True)
        self.remote = {"repo_id": "test/data", "prefix": self.prefix, "revision": "main"}
        self.manifest = {
            "files": ["run1.nc", "run2.nc"], "planned_runs": 2, "failures": [],
            "valid_times": ["2023-01-01T12:30:00Z"], "uploads": {},
        }
        for name in self.manifest["files"]:
            artifacts = {}
            for artifact in (name, Path(name).with_suffix(".metadata.json").name):
                content = artifact.encode()
                digest = hashlib.sha256(content).hexdigest()
                # Model the real Hub cache: snapshot files point into blobs.
                blob = self.root / "cache" / "blobs" / digest
                blob.parent.mkdir(exist_ok=True)
                blob.write_bytes(content)
                (self.snapshot / artifact).symlink_to(blob)
                artifacts[artifact] = {"size": len(content), "sha256": digest}
            self.manifest["uploads"][name] = {"files": artifacts}
        self.write_remote_manifest()
        self.api = Mock()
        self.api.repo_info.return_value = SimpleNamespace(sha=self.revision)
        self.download = Mock(side_effect=self.cached_download)
        self.hub = SimpleNamespace(HfApi=Mock(return_value=self.api), hf_hub_download=self.download)
        self.patch = patch.dict("sys.modules", {"huggingface_hub": self.hub})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def cached_download(self, **kwargs):
        self.assertEqual(kwargs["repo_id"], "test/data")
        self.assertEqual(kwargs["repo_type"], "dataset")
        self.assertEqual(kwargs["revision"], self.revision)
        path = self.snapshot / Path(kwargs["filename"]).name
        if not path.is_file():
            raise FileNotFoundError(kwargs["filename"])
        return str(path)

    def write_remote_manifest(self):
        (self.snapshot / "manifest.json").write_text(json.dumps(self.manifest))

    def copy_local_run(self, name):
        for artifact in (name, Path(name).with_suffix(".metadata.json").name):
            shutil.copyfile(self.snapshot / artifact, self.local.parent / artifact)

    def write_config(self, entries):
        path = self.root / "config.json"
        path.write_text(json.dumps({"geos_manifests": entries, "sources": {}}))
        return path

    def test_complete_local_collection_never_contacts_hub(self):
        self.local.write_text(json.dumps(self.manifest))
        for name in self.manifest["files"]:
            self.copy_local_run(name)
        cfg = load_config(self.write_config([{"path": "local/manifest.json", "huggingface": self.remote}]))
        self.assertEqual(cfg["geos_files"], [str(self.local.parent / n) for n in self.manifest["files"]])
        self.hub.HfApi.assert_not_called()
        self.download.assert_not_called()

    def test_missing_manifest_pins_downloads_and_preserves_snapshot_sidecars(self):
        cfg = load_config(self.write_config([{"path": "local/manifest.json", "huggingface": self.remote}]))
        self.api.repo_info.assert_called_once_with("test/data", repo_type="dataset", revision="main")
        self.assertEqual(self.download.call_count, 5)  # manifest + two NetCDF/metadata pairs
        self.assertEqual(cfg["geos_resolved_manifests"][0]["revision"], self.revision)
        self.assertEqual(cfg["geos_valid_times"], self.manifest["valid_times"])
        self.assertEqual(cfg["required_product"], "forecast")
        for filename in cfg["geos_files"]:
            self.assertEqual(Path(filename).parent, self.snapshot)
            self.assertTrue(Path(filename).with_suffix(".metadata.json").is_file())
        self.assertFalse(self.local.exists())  # cache fallback does not overwrite inputs

    def test_partial_local_collection_reuses_only_matching_pairs(self):
        self.local.write_text(json.dumps(self.manifest))
        self.copy_local_run("run1.nc")
        _, files, _ = resolve_forecast_manifest(self.local, self.remote)
        self.assertEqual(files, [str(self.local.parent / "run1.nc"), str(self.snapshot / "run2.nc")])
        self.assertEqual([c.kwargs["filename"] for c in self.download.call_args_list], [
            f"{self.prefix}/manifest.json", f"{self.prefix}/run2.nc", f"{self.prefix}/run2.metadata.json",
        ])

    def test_changed_local_file_uses_pinned_copy_without_overwriting(self):
        self.local.write_text(json.dumps(self.manifest))
        self.copy_local_run("run1.nc")
        (self.local.parent / "run1.nc").write_bytes(b"changed")
        _, files, _ = resolve_forecast_manifest(self.local, self.remote)
        self.assertEqual(files[0], str(self.snapshot / "run1.nc"))
        self.assertEqual((self.local.parent / "run1.nc").read_bytes(), b"changed")

    def test_legacy_manifest_metadata_and_commit_pin_need_no_api_lookup(self):
        self.manifest["huggingface"] = dict(self.remote, revision=self.revision)
        self.local.write_text(json.dumps(self.manifest))
        cfg = load_config(self.write_config(["local/manifest.json"]))
        self.assertEqual(len(cfg["geos_files"]), 2)
        self.hub.HfApi.assert_not_called()
        self.assertTrue(all(c.kwargs["revision"] == self.revision for c in self.download.call_args_list))

    def test_multiple_collections_can_mix_local_and_remote(self):
        other = self.root / "other"
        other.mkdir()
        (other / "manifest.json").write_text(json.dumps(self.manifest))
        for name in self.manifest["files"]:
            (other / name).touch()
        cfg = load_config(self.write_config([
            "other/manifest.json", {"path": "local/manifest.json", "huggingface": self.remote},
        ]))
        self.assertEqual(len(cfg["geos_files"]), 4)
        self.assertEqual(len(cfg["geos_resolved_manifests"]), 2)
        self.assertEqual(self.api.repo_info.call_count, 1)

    def test_missing_files_without_fallback_are_actionable(self):
        with self.assertRaisesRegex(FileNotFoundError, "repo_id/prefix"):
            resolve_forecast_manifest(self.local)
        self.download.assert_not_called()

    def test_network_and_missing_sidecar_fail_before_preparation(self):
        self.download.side_effect = OSError("offline")
        with self.assertRaisesRegex(RuntimeError, "test/data/geos_cf/forecasts.*offline"):
            resolve_forecast_manifest(self.local, self.remote)
        self.download.side_effect = self.cached_download
        (self.snapshot / "run1.metadata.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "run1.metadata.json"):
            resolve_forecast_manifest(self.local, self.remote)

    def test_incomplete_manifest_requires_explicit_opt_in(self):
        self.manifest["planned_runs"] = 3
        self.write_remote_manifest()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            resolve_forecast_manifest(self.local, self.remote)
        self.assertEqual(self.download.call_count, 1)  # fail before downloading runs
        _, files, _ = resolve_forecast_manifest(self.local, self.remote, allow_partial=True)
        self.assertEqual(len(files), 2)
        self.manifest["files"] = []
        self.write_remote_manifest()
        with self.assertRaisesRegex(ValueError, "no downloaded runs"):
            resolve_forecast_manifest(self.local, self.remote, allow_partial=True)

    def test_manifest_cannot_reference_files_outside_its_folder(self):
        self.manifest["files"] = ["../other.nc"]
        self.write_remote_manifest()
        with self.assertRaisesRegex(ValueError, "relative basenames"):
            resolve_forecast_manifest(self.local, self.remote)


if __name__ == "__main__":
    unittest.main()
