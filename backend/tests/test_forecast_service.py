import numpy as np
from app.services.forecast_service import generate_forecasts


def test_generate_forecasts_shapes():
    x = np.load("app/data/latest_observed_sequence.npy")
    out = generate_forecasts(x)

    assert "24h" in out
    assert "48h" in out
    assert "72h" in out

    assert out["24h"].shape == (3, 56, 96)
    assert out["48h"].shape == (3, 56, 96)
    assert out["72h"].shape == (3, 56, 96)