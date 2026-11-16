"""Real-ingestion CLI safety. All provider behavior is mocked only in tests."""
from types import SimpleNamespace
import pytest
from scripts import publish_nasa_forecast as job


def test_bbox_and_cli_validation():
    assert job.parse_bbox("33.45,34.05,-84.8,-83.95")["min_lat"] == 33.45
    with pytest.raises(job.argparse.ArgumentTypeError):
        job.parse_bbox("33,34")
    with pytest.raises(SystemExit):
        job.main(["--deadline", "999"])


def test_failures_are_sanitized_without_provider_responses(monkeypatch, capsys):
    def fail(_):
        raise ValueError("private-credential-placeholder")
    monkeypatch.setattr(job, "worker", fail)
    assert job.main(["--worker"]) == 1
    output = capsys.readouterr().err
    assert "ValueError" in output and "private-credential-placeholder" not in output


def test_deadline_cancels_whole_worker_group(monkeypatch):
    calls = []
    class Process:
        pid = 12345
        def wait(self, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise job.subprocess.TimeoutExpired("test-worker", timeout)
            return -9
    monkeypatch.setattr(job.subprocess, "Popen", lambda *a, **kw: Process())
    def taskkill(*a, **kw):
        calls.append(a[0])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(job.subprocess, "run", taskkill)
    if job.sys.platform != "win32":
        monkeypatch.setattr(job.os, "killpg", lambda *a: calls.append(a))
    assert job.main(["--deadline", "10"]) == 1
    assert calls[0] == 10 and calls[-1] == 10 and len(calls) == 3


def test_no_model_or_synthetic_mode_is_available():
    for forbidden in (["--mode", "sample"], ["--database"], ["--model", "placeholder"]):
        with pytest.raises(SystemExit):
            job.main(forbidden)
