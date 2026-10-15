import numpy as np


def compute_mae_improvement(model_prediction, target, persistence_baseline):
    """
    Compute MAE for the model vs. a persistence baseline and return the percent improvement.

    Parameters
    ----------
    model_prediction : array-like
        Model forecast with the same shape as target.
    target : array-like
        Ground-truth values to evaluate against.
    persistence_baseline : array-like
        Naive baseline, often the last observed frame.

    Returns
    -------
    dict
        Includes MAE values and percent improvement.
    """
    pred = np.asarray(model_prediction, dtype=np.float64)
    true = np.asarray(target, dtype=np.float64)
    persist = np.asarray(persistence_baseline, dtype=np.float64)

    if pred.shape != true.shape or pred.shape != persist.shape:
        raise ValueError(
            "model_prediction, target, and persistence_baseline must all have the same shape. "
            f"Got {pred.shape}, {true.shape}, and {persist.shape}."
        )

    mae_model = float(np.mean(np.abs(pred - true)))
    mae_persistence = float(np.mean(np.abs(persist - true)))

    if np.isclose(mae_persistence, 0.0):
        raise ValueError("Persistence MAE is zero; percent improvement is undefined.")

    percent_improvement = float(
        ((mae_persistence - mae_model) / mae_persistence) * 100.0
    )

    return {
        "mae_model": mae_model,
        "mae_persistence": mae_persistence,
        "percent_improvement": percent_improvement,
        "improved": mae_model < mae_persistence,
    }


TEMPORAL_STD_EPSILON = 1e-6


def _as_float_array(name, value):
    array = np.asarray(value, dtype=np.float64)
    if array.ndim < 2:
        raise ValueError(
            f"{name} must have a time axis plus spatial axes, got shape {array.shape}."
        )
    return array


def _shared_valid_mask(prediction, target, persistence, wrong_day, mask=None):
    valid = (
        np.isfinite(prediction)
        & np.isfinite(target)
        & np.isfinite(persistence)
        & np.isfinite(wrong_day)
    )
    if mask is not None:
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != prediction.shape:
            raise ValueError(
                "mask must have the same shape as prediction. "
                f"Got {mask.shape} and {prediction.shape}."
            )
        valid = valid & mask
    if not np.any(valid):
        raise ValueError("No valid cells remain after masking non-finite values.")
    return valid


def _mean_on_mask(values, valid):
    return float(np.mean(values[valid]))


def masked_error_metrics(prediction, target, valid):
    """MAE, RMSE, and bias on cells where ``valid`` is true.

    Bias is mean(prediction - target). Positive bias is overprediction.
    """
    error = prediction - target
    return {
        "mae": _mean_on_mask(np.abs(error), valid),
        "rmse": float(np.sqrt(_mean_on_mask(error * error, valid))),
        "bias": _mean_on_mask(error, valid),
    }


def daily_spatial_means(frames, valid):
    """Mean of each time slice over the valid spatial cells."""
    flat_values = frames.reshape(frames.shape[0], -1)
    flat_valid = valid.reshape(valid.shape[0], -1)
    totals = np.where(flat_valid, flat_values, 0.0).sum(axis=1)
    counts = flat_valid.sum(axis=1)
    means = np.full(frames.shape[0], np.nan, dtype=np.float64)
    observed = counts > 0
    means[observed] = totals[observed] / counts[observed]
    return means


def daily_mean_correlation(prediction, target, valid):
    """Pearson correlation of daily spatial means.

    Returns None when fewer than two days are available or either series
    does not vary. A constant forecast therefore cannot receive a positive
    correlation by accident.
    """
    predicted_means = daily_spatial_means(prediction, valid)
    observed_means = daily_spatial_means(target, valid)
    paired = np.isfinite(predicted_means) & np.isfinite(observed_means)
    if int(paired.sum()) < 2:
        return None

    predicted_means = predicted_means[paired]
    observed_means = observed_means[paired]
    if np.isclose(predicted_means.std(), 0.0) or np.isclose(observed_means.std(), 0.0):
        return None
    return float(np.corrcoef(predicted_means, observed_means)[0, 1])


def temporal_std(prediction, valid):
    """Mean per-cell standard deviation across time, on valid cells."""
    counts = valid.sum(axis=0)
    observed = counts > 0
    if not np.any(observed):
        return 0.0

    totals = np.where(valid, prediction, 0.0).sum(axis=0)
    means = np.zeros(counts.shape, dtype=np.float64)
    means[observed] = totals[observed] / counts[observed]
    deviations = np.where(valid, prediction - means, 0.0)
    variance = np.zeros(counts.shape, dtype=np.float64)
    variance[observed] = (deviations * deviations).sum(axis=0)[observed] / counts[observed]
    return float(np.mean(np.sqrt(variance[observed])))


def percent_skill(mae_model, mae_reference):
    """Percent MAE reduction against ``mae_reference``.

    The denominator is the reference MAE on the same cells and days.
    Returns None when that denominator is zero, because the percentage
    is then undefined.
    """
    if not np.isfinite(mae_reference) or np.isclose(mae_reference, 0.0):
        return None
    return float((mae_reference - mae_model) / mae_reference * 100.0)


def score_forecast(prediction, target, persistence, wrong_day, mask=None, epsilon=TEMPORAL_STD_EPSILON):
    """Score one channel at one horizon against truth, persistence, and chance.

    ``prediction``, ``target``, ``persistence``, and ``wrong_day`` share
    shape ``(n_days, ...)``. Persistence is the last observed frame carried
    forward. ``wrong_day`` is the observed target from a different origin,
    used as a chance baseline of a real map on the wrong date.

    A forecast passes only when its MAE is lower than the wrong-day MAE,
    the daily spatial means are positively correlated, the prediction
    varies over time, and every prediction value is finite.
    """
    prediction = _as_float_array("prediction", prediction)
    target = _as_float_array("target", target)
    persistence = _as_float_array("persistence", persistence)
    wrong_day = _as_float_array("wrong_day", wrong_day)

    if not (prediction.shape == target.shape == persistence.shape == wrong_day.shape):
        raise ValueError(
            "prediction, target, persistence, and wrong_day must all have the same shape. "
            f"Got {prediction.shape}, {target.shape}, {persistence.shape}, and {wrong_day.shape}."
        )

    valid = _shared_valid_mask(prediction, target, persistence, wrong_day, mask)
    model_errors = masked_error_metrics(prediction, target, valid)
    persistence_mae = _mean_on_mask(np.abs(persistence - target), valid)
    wrong_day_mae = _mean_on_mask(np.abs(wrong_day - target), valid)
    correlation = daily_mean_correlation(prediction, target, valid)
    prediction_temporal_std = temporal_std(prediction, valid)

    finite = bool(np.all(np.isfinite(prediction)))
    beats_wrong_day = bool(model_errors["mae"] < wrong_day_mae)
    positive_correlation = correlation is not None and correlation > 0.0
    varies_over_time = bool(prediction_temporal_std > epsilon)

    failure_reasons = []
    if not finite:
        failure_reasons.append("prediction_has_nonfinite_values")
    if not beats_wrong_day:
        failure_reasons.append("mae_not_below_wrong_day")
    if not positive_correlation:
        failure_reasons.append("daily_mean_correlation_not_positive")
    if not varies_over_time:
        failure_reasons.append("prediction_does_not_vary_over_time")

    return {
        "mae": model_errors["mae"],
        "rmse": model_errors["rmse"],
        "bias": model_errors["bias"],
        "mae_persistence": persistence_mae,
        "mae_wrong_day": wrong_day_mae,
        "percent_skill_vs_persistence": percent_skill(model_errors["mae"], persistence_mae),
        "correlation": correlation,
        "temporal_std": prediction_temporal_std,
        "n_days": int(prediction.shape[0]),
        "n_values": int(valid.sum()),
        "finite": finite,
        "beats_wrong_day": beats_wrong_day,
        "positive_correlation": bool(positive_correlation),
        "varies_over_time": varies_over_time,
        "passes_not_random": len(failure_reasons) == 0,
        "failure_reasons": failure_reasons,
    }
