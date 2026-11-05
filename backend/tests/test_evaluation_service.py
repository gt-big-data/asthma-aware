import numpy as np
import pytest

from app.services.evaluation_service import compute_mae_improvement, score_forecast


def test_compute_mae_improvement_reports_persistence_gain():
    prediction = np.array([
        [1.0, 2.0],
        [3.0, 4.0],
    ])
    target = np.array([
        [2.0, 2.0],
        [2.0, 6.0],
    ])
    persistence = np.array([
        [5.0, 1.0],
        [3.0, 5.0],
    ])

    result = compute_mae_improvement(prediction, target, persistence)

    assert np.isclose(result["mae_model"], 1.0)
    assert np.isclose(result["mae_persistence"], 1.5)
    assert np.isclose(result["percent_improvement"], 33.3333333333)
    assert result["improved"] is True


def test_compute_mae_improvement_raises_for_zero_persistence_mae():
    prediction = np.array([1.0, 2.0])
    target = np.array([1.0, 2.0])
    persistence = np.array([1.0, 2.0])

    try:
        compute_mae_improvement(prediction, target, persistence)
        raise AssertionError("Expected ValueError for zero persistence MAE")
    except ValueError:
        pass


def _varying_frames():
    return np.stack(
        [np.full((2, 2), value, dtype=np.float64) for value in (0.0, 1.0, 2.0, 3.0)]
    )


def _wrong_day(frames):
    shift = frames.shape[0] // 2
    return np.stack([frames[(index + shift) % frames.shape[0]] for index in range(frames.shape[0])])


def test_score_forecast_passes_when_prediction_matches_next_frame():
    target = _varying_frames()
    persistence = np.stack(
        [np.full((2, 2), value, dtype=np.float64) for value in (5.0, 0.0, 1.0, 2.0)]
    )

    result = score_forecast(target, target, persistence, _wrong_day(target))

    assert result["passes_not_random"] is True
    assert result["failure_reasons"] == []
    assert np.isclose(result["mae"], 0.0)
    assert np.isclose(result["correlation"], 1.0)
    assert result["beats_wrong_day"] is True
    assert result["percent_skill_vs_persistence"] is not None
    assert result["percent_skill_vs_persistence"] > 0.0


def test_score_forecast_fails_when_prediction_is_the_wrong_day():
    target = _varying_frames()
    wrong_day = _wrong_day(target)
    persistence = np.zeros_like(target)

    result = score_forecast(wrong_day, target, persistence, wrong_day)

    assert result["beats_wrong_day"] is False
    assert result["passes_not_random"] is False
    assert "mae_not_below_wrong_day" in result["failure_reasons"]


def test_score_forecast_fails_when_prediction_is_constant():
    target = _varying_frames()
    constant = np.full_like(target, 1.5)

    result = score_forecast(constant, target, np.zeros_like(target), _wrong_day(target))

    assert result["varies_over_time"] is False
    assert result["passes_not_random"] is False
    assert "prediction_does_not_vary_over_time" in result["failure_reasons"]


def test_score_forecast_raises_when_shapes_differ():
    target = _varying_frames()

    with pytest.raises(ValueError, match="same shape"):
        score_forecast(target, target[:-1], target, target)


def test_evaluate_series_scores_each_horizon_against_that_future_day():
    from app.ml.forecast_eval import evaluate_series

    series = np.zeros((8, 3, 2, 2), dtype=np.float32)
    for day in range(series.shape[0]):
        series[day] = day

    def oracle(window):
        last = float(window[-1, 0, 0, 0])
        return {
            "24h": np.full((3, 2, 2), last + 1, dtype=np.float32),
            "48h": np.full((3, 2, 2), last + 2, dtype=np.float32),
            "72h": np.full((3, 2, 2), last + 3, dtype=np.float32),
        }

    report = evaluate_series(series, forecast_fn=oracle)

    for horizon in ("24h", "48h", "72h"):
        score = report["horizons"][horizon]["so2"]
        assert score["passes_not_random"] is True
        assert np.isclose(score["mae"], 0.0)
        assert score["mae"] < score["mae_wrong_day"]


def test_masked_mae_ignores_cells_outside_the_mask():
    target = _varying_frames()
    prediction = target.copy()
    prediction[:, 0, 0] = 100.0
    mask = np.ones(target.shape, dtype=bool)
    mask[:, 0, 0] = False

    result = score_forecast(prediction, target, target, _wrong_day(target), mask=mask)

    assert np.isclose(result["mae"], 0.0)
