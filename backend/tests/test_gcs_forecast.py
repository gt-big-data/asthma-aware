from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from google.api_core.exceptions import Forbidden, PreconditionFailed

from app.services import gcs_forecast as gcs
from app.services.geos_dashboard import PublishedForecast


def forecast(issued_at):
    return PublishedForecast(
        source_run="test", issued_at=issued_at, times=[issued_at],
        latitudes=[33.75], longitudes=[-84.5], pm25=[[10.0]],
    )


def test_failed_upload_keeps_current_pointer(monkeypatch, tmp_path):
    bucket = Mock()
    monkeypatch.setattr(gcs, "get_bucket", lambda name: bucket)
    run = forecast(datetime(2026, 11, 12, 9, tzinfo=timezone.utc))
    bucket.blob.return_value.upload_from_string.side_effect = Forbidden("upload denied")

    with pytest.raises(Forbidden):
        gcs.publish_gcs("test", tmp_path / "forecast.nc", run)

    bucket.get_blob.assert_not_called()
    assert all(call.args[0] != gcs.CURRENT for call in bucket.blob.call_args_list)


@pytest.mark.parametrize("asset", ["layers", "risk", "history"])
def test_failed_derived_upload_keeps_current_pointer(monkeypatch, tmp_path, asset):
    bucket = Mock()
    layers_blob = Mock()
    layers_blob.upload_from_filename.side_effect = Forbidden("layers upload denied")
    suffix = "/downscaled.nc" if asset == "layers" else "/risk.npz"
    bucket.blob.side_effect = lambda key: layers_blob if key.endswith(suffix) else Mock()
    monkeypatch.setattr(gcs, "get_bucket", lambda name: bucket)
    run = forecast(datetime(2026, 11, 12, 9, tzinfo=timezone.utc))
    layers = tmp_path / "downscaled.nc"

    with pytest.raises(Forbidden):
        gcs.publish_gcs("test", tmp_path / "forecast.nc", run, **{asset: layers})

    layers_blob.upload_from_filename.assert_called_once()
    assert layers_blob.upload_from_filename.call_args.args == (layers,)
    bucket.get_blob.assert_not_called()


def test_concurrent_newer_publication_wins_and_can_be_read(monkeypatch, tmp_path):
    bucket = Mock()
    monkeypatch.setattr(gcs, "get_bucket", lambda name: bucket)
    incoming = forecast(datetime(2026, 11, 12, 9, tzinfo=timezone.utc))
    newer = forecast(datetime(2026, 11, 13, 9, tzinfo=timezone.utc))
    selected = gcs.Publication(publication_id=uuid4(), issued_at=newer.issued_at)
    current = Mock(generation=17)
    current.download_as_bytes.return_value = selected.model_dump_json().encode()
    bucket.get_blob.side_effect = [None, current]
    artifact = Mock()
    artifact.download_as_bytes.return_value = newer.model_dump_json().encode()
    current.upload_from_string.side_effect = PreconditionFailed("pointer was created")
    bucket.blob.side_effect = lambda key: (
        current if key == gcs.CURRENT else artifact if key == selected.artifact else Mock()
    )

    result = gcs.publish_gcs("test", tmp_path / "forecast.nc", incoming)
    assert result == selected
    assert current.upload_from_string.call_count == 1
    assert current.upload_from_string.call_args.kwargs["if_generation_match"] == 0
    assert gcs.load_gcs_run("test") == newer
    assert gcs.load_gcs_run("test") == newer
    artifact.download_as_bytes.assert_called_once()
