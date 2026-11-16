"""Offline checks for the isolated infrastructure sample job."""

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import subprocess
import sys
from uuid import UUID

from forecast_job.__main__ import main, run_once
from forecast_job.sample import generate_sample_forecast


def test_sample_schema_and_determinism():
    now = datetime(2026, 11, 2, 12, 34, tzinfo=timezone.utc)
    artifact = generate_sample_forecast("fixed-run", now)
    assert artifact == generate_sample_forecast("fixed-run", now)
    assert artifact["schema_version"] == "1.0"
    assert artifact["run_id"] == "fixed-run"
    assert artifact["source"] == "sample"
    assert artifact["generated_at"] == "2026-11-02T12:34:00Z"
    parse = datetime.fromisoformat
    start, end = map(parse, (artifact["valid_from"], artifact["valid_until"]))
    assert start > now and end - start == timedelta(hours=3)
    assert len(artifact["forecasts"]) == 3
    bounds = artifact["bounds"]
    for index, frame in enumerate(artifact["forecasts"]):
        assert parse(frame["valid_at"]) == start + timedelta(hours=index)
        assert len(frame["cells"]) == 2
        for cell in frame["cells"]:
            assert bounds["min_lat"] <= cell["lat"] <= bounds["max_lat"]
            assert bounds["min_lon"] <= cell["lon"] <= bounds["max_lon"]
            assert isinstance(cell["sample_index"], int)


def test_runs_write_distinct_artifacts_and_log_lifecycle(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="forecast_job"):
        first = run_once("sample", tmp_path)
        second = run_once("sample", tmp_path)
    a, b = (json.loads(path.read_text(encoding="utf-8")) for path in (first, second))
    assert first != second and a["run_id"] != b["run_id"]
    UUID(a["run_id"])
    assert a["run_id"] in first.name
    for event in ("start run_id=", "processing run_id=", "output run_id=", "status=success"):
        assert event in caplog.text


def test_output_failure_returns_nonzero_and_logs(tmp_path, caplog):
    blocked = tmp_path / "file-not-directory"
    blocked.write_text("preserve me", encoding="utf-8")
    assert main(["--mode", "sample", "--output-dir", str(blocked)]) == 1
    assert "status=failed" in caplog.text
    assert blocked.read_text(encoding="utf-8") == "preserve me"


def test_processing_failure_returns_nonzero(tmp_path, monkeypatch, caplog):
    def fail(*args):
        raise RuntimeError("processing failed")
    monkeypatch.setattr("forecast_job.__main__.generate_sample_forecast", fail)
    assert main(["--mode", "sample", "--output-dir", str(tmp_path)]) == 1
    assert "processing failed" in caplog.text and "status=failed" in caplog.text
    assert not list(tmp_path.iterdir())


def test_output_directory_environment_and_cli_override(tmp_path, monkeypatch):
    environment_dir = tmp_path / "environment"
    explicit_dir = tmp_path / "explicit"
    monkeypatch.setenv("FORECAST_OUTPUT_DIR", str(environment_dir))
    assert main(["--mode", "sample"]) == 0
    assert len(list(environment_dir.glob("forecast-*.json"))) == 1
    assert main(["--mode", "sample", "--output-dir", str(explicit_dir)]) == 0
    assert len(list(explicit_dir.glob("forecast-*.json"))) == 1
    assert len(list(environment_dir.glob("forecast-*.json"))) == 1


def test_cli_requires_explicit_mode_and_uses_no_site_packages(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    base = [sys.executable, "-B", "-S", "-m", "forecast_job"]
    missing = subprocess.run(base, cwd=backend, capture_output=True, text=True)
    assert missing.returncode == 2
    result = subprocess.run(base + ["--mode", "sample", "--output-dir", str(tmp_path)], cwd=backend, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "status=success" in result.stdout
    assert len(list(tmp_path.glob("forecast-*.json"))) == 1
    blocked = tmp_path / "blocked"
    blocked.write_text("existing file", encoding="utf-8")
    failed = subprocess.run(base + ["--mode", "sample", "--output-dir", str(blocked)], cwd=backend, capture_output=True, text=True)
    assert failed.returncode == 1
    assert "status=failed" in failed.stdout
