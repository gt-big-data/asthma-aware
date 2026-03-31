import numpy as np
from app.services.ml_model_service import predict_next_frame


def test_predict_next_frame_shape():
    x = np.load("app/data/latest_observed_sequence.npy")
    y = predict_next_frame(x)

    assert isinstance(y, np.ndarray)
    assert y.shape == (3, 56, 96)