"""Scaler arithmetic. No database needed.

The whole point of storing scaler constants is that normalisation stops
moving between runs, so these tests pin down the arithmetic and the
failure modes that would otherwise be silent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from db.repositories.scalers import (  # noqa: E402
    Scaler,
    inverse_transform_frame,
    transform_sequence,
)


def minmax(vmin: float, vmax: float, slug: str = "no2") -> Scaler:
    return Scaler(
        variable_slug=slug,
        method="minmax",
        version="v1",
        fit_min=vmin,
        fit_max=vmax,
        fit_mean=None,
        fit_std=None,
    )


def test_minmax_maps_bounds_to_zero_and_one():
    scaler = minmax(10.0, 20.0)
    assert scaler.transform(np.array([10.0])) == pytest.approx(0.0)
    assert scaler.transform(np.array([20.0])) == pytest.approx(1.0)
    assert scaler.transform(np.array([15.0])) == pytest.approx(0.5)


def test_minmax_round_trips():
    scaler = minmax(1.5e-5, 6.8e-5)  # realistic NO2 mol/m^2 range
    original = np.array([1.5e-5, 3.0e-5, 6.8e-5], dtype=np.float32)
    restored = scaler.inverse_transform(scaler.transform(original))
    np.testing.assert_allclose(restored, original, rtol=1e-6)


def test_values_outside_the_fit_range_are_not_clipped():
    """A new extreme should exceed [0,1], not be silently clamped.

    Clamping would hide distribution drift; letting it run past 1.0 means
    a genuinely unprecedented reading stays visible in the model input.
    """
    scaler = minmax(0.0, 10.0)
    assert scaler.transform(np.array([15.0])) == pytest.approx(1.5)
    assert scaler.transform(np.array([-5.0])) == pytest.approx(-0.5)


def test_nan_is_preserved_through_scaling():
    """Missing data must stay missing, not become a number."""
    scaler = minmax(0.0, 10.0)
    out = scaler.transform(np.array([np.nan, 5.0]))
    assert np.isnan(out[0])
    assert out[1] == pytest.approx(0.5)


def test_constant_variable_does_not_divide_by_zero():
    scaler = minmax(4.2, 4.2)
    out = scaler.transform(np.array([4.2, 4.2]))
    assert np.all(out == 0.0)
    assert np.all(np.isfinite(out))


def test_negative_minimum_is_handled():
    """S5P SO2 legitimately retrieves negative column densities."""
    scaler = minmax(-0.00085, 0.00332)
    out = scaler.transform(np.array([-0.00085, 0.00332]))
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(1.0)


def test_standard_scaler_round_trips():
    scaler = Scaler(
        variable_slug="ndvi",
        method="standard",
        version="v1",
        fit_min=None,
        fit_max=None,
        fit_mean=0.5,
        fit_std=0.25,
    )
    original = np.array([0.25, 0.5, 0.75], dtype=np.float32)
    np.testing.assert_allclose(scaler.transform(original), [-1.0, 0.0, 1.0], atol=1e-6)
    np.testing.assert_allclose(
        scaler.inverse_transform(scaler.transform(original)), original, rtol=1e-6
    )


def test_transform_sequence_applies_per_channel():
    """Each channel must use its own constants.

    The failure this guards against -- scaling NO2 with NDVI's bounds --
    produces plausible-looking numbers and no error.
    """
    scalers = [minmax(0.0, 10.0, "so2"), minmax(0.0, 100.0, "ndvi"), minmax(0.0, 1.0, "no2")]
    sequence = np.ones((4, 3, 2, 2), dtype=np.float32)
    sequence[:, 0] = 5.0
    sequence[:, 1] = 50.0
    sequence[:, 2] = 0.5

    out = transform_sequence(sequence, scalers)
    assert out.shape == sequence.shape
    np.testing.assert_allclose(out[:, 0], 0.5)
    np.testing.assert_allclose(out[:, 1], 0.5)
    np.testing.assert_allclose(out[:, 2], 0.5)


def test_transform_sequence_rejects_channel_count_mismatch():
    scalers = [minmax(0.0, 1.0), minmax(0.0, 1.0)]
    with pytest.raises(ValueError, match="3 channels but 2 scalers"):
        transform_sequence(np.zeros((4, 3, 2, 2), dtype=np.float32), scalers)


def test_transform_sequence_rejects_wrong_rank():
    with pytest.raises(ValueError, match=r"\(T, C, H, W\)"):
        transform_sequence(np.zeros((3, 2, 2), dtype=np.float32), [minmax(0, 1)])


def test_inverse_transform_frame_round_trips_model_output():
    """(C, H, W) model output back into real units."""
    scalers = [minmax(0.0, 10.0, "so2"), minmax(0.0, 100.0, "ndvi"), minmax(0.0, 1.0, "no2")]
    frame = np.full((3, 56, 96), 0.25, dtype=np.float32)
    out = inverse_transform_frame(frame, scalers)
    assert out.shape == (3, 56, 96)
    np.testing.assert_allclose(out[0], 2.5)
    np.testing.assert_allclose(out[1], 25.0)
    np.testing.assert_allclose(out[2], 0.25)


def test_inverse_transform_frame_rejects_wrong_rank():
    with pytest.raises(ValueError, match=r"\(C, H, W\)"):
        inverse_transform_frame(np.zeros((4, 3, 56, 96), dtype=np.float32), [minmax(0, 1)])
