"""Deployment import/dependency and URL capability boundaries, no cloud calls."""
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import os
import shutil

import pytest

from app.config import Settings
from app.services.forecast_delivery import DeliveryUnavailable, sign_delivery_url


def declared_requirements(path):
    result = set()
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("-r "):
            result.update(declared_requirements(path.parent / line[3:].strip()))
        elif line:
            import re
            result.add(re.split(r"[<=>\[ ]", line, maxsplit=1)[0].lower())
    return result


def test_fresh_cloud_api_dependency_manifest_includes_http_transport():
    requirements = declared_requirements(Path(__file__).parents[1] / "requirements/cloud-backend.txt")
    # Places is imported by the production API. Test environments install httpx
    # independently, which can hide a broken fresh container dependency manifest.
    assert {"httpx", "google-cloud-tasks", "google-auth", "psycopg"} <= requirements


def test_production_import_does_not_load_legacy_scoring_models_or_acquisition():
    script = """import json,sys
import app.cloud
forbidden=('app.services.live_forecast','forecast_pipeline.models','torch','ingestion.geos_cf.fetch_forecast')
print(json.dumps([name for name in forbidden if name in sys.modules]))
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parents[1],
                            capture_output=True, text=True, timeout=20, check=True)
    assert result.stdout.strip() == "[]", result.stdout


@pytest.mark.parametrize("image,module", [("api", "app.cloud"), ("worker", "app.worker")])
def test_declared_container_python_sources_import_in_isolation(tmp_path, image, module):
    backend = Path(__file__).parents[1]
    stage = tmp_path / image
    stage.mkdir()
    for line in (backend / f"Dockerfile.{image}").read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        arguments = [arg for arg in line.split()[1:] if not arg.startswith("--")]
        sources, target = arguments[:-1], arguments[-1]
        destination = stage / target.removeprefix("/srv/")
        destination.mkdir(parents=True, exist_ok=True)
        for name in sources:
            source = backend / name.rstrip("/")
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("reference", "__pycache__", "*.pyc"))
            else:
                shutil.copy2(source, destination / source.name)
    assert not (stage / "app/main.py").exists()
    assert not (stage / "app/services/live_forecast.py").exists()
    script = f"import importlib; importlib.import_module('{module}'); print('isolated entrypoint imported')"
    environment = {**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"}
    completed = subprocess.run([sys.executable, "-c", script], cwd=stage, env=environment,
                               capture_output=True, text=True, timeout=20, check=True)
    assert "isolated entrypoint imported" in completed.stdout


@pytest.mark.parametrize("name", ["raw/run/private.nc", "app/symptom_logs/user.json", "processed/../raw/input.nc",
    "processed/%2e%2e/private", "processed/run/tile.json?Signature=attacker", "processed//tile.json",
    "https://attacker.example/processed/tile.json", "processed/run/tile.json#private"])
def test_cdn_signer_never_issues_private_or_ambiguous_path_capability(name):
    now = datetime.now(timezone.utc)
    settings = Settings(forecast_delivery_base_url="https://cdn.example.com", forecast_cdn_key_name="test-key",
                        forecast_cdn_signing_key=base64.urlsafe_b64encode(b"0123456789abcdef").decode())
    with pytest.raises(DeliveryUnavailable):
        sign_delivery_url(settings, name, now=now, valid_until=now+timedelta(hours=1))
