import numpy as np
from app.services.preprocessing_service import (
    preprocess_input_sequence,
    postprocess_output_frame,
)
from app.services.ml_model_service import predict_next_frame


def generate_forecasts(latest_sequence: np.ndarray):
    """
    latest_sequence shape: (4, 3, 56, 96)

    Returns:
    {
        "24h": np.ndarray shape (3, 56, 96),
        "48h": np.ndarray shape (3, 56, 96),
        "72h": np.ndarray shape (3, 56, 96),
    }
    """
    seq = preprocess_input_sequence(latest_sequence)

    pred_24 = predict_next_frame(seq)
    pred_24 = postprocess_output_frame(pred_24)

    seq_48 = np.concatenate([seq[1:], pred_24[np.newaxis, ...]], axis=0)
    pred_48 = predict_next_frame(seq_48)
    pred_48 = postprocess_output_frame(pred_48)

    seq_72 = np.concatenate([seq_48[1:], pred_48[np.newaxis, ...]], axis=0)
    pred_72 = predict_next_frame(seq_72)
    pred_72 = postprocess_output_frame(pred_72)

    return {
        "24h": pred_24,
        "48h": pred_48,
        "72h": pred_72,
    }