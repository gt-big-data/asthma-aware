from pathlib import Path
import numpy as np
import torch
import __main__

from app.ml.model_definition import ConvLSTM, ConvLSTMCell

MODEL_PATH = Path(__file__).resolve().parent.parent / "ml" / "base_convlstm.pt"

_model = None


def load_model():
    global _model

    if _model is None:
        # Register the classes under __main__ so torch.load can find them
        __main__.ConvLSTM = ConvLSTM
        __main__.ConvLSTMCell = ConvLSTMCell

        _model = torch.load(MODEL_PATH, map_location="cpu", weights_only=False)
        _model.eval()

    return _model


def predict_next_frame(input_sequence: np.ndarray) -> np.ndarray:
    """
    input_sequence shape: (4, 3, 56, 96)
    returns shape: (3, 56, 96)
    """
    if input_sequence.shape != (4, 3, 56, 96):
        raise ValueError(
            f"Expected input shape (4, 3, 56, 96), got {input_sequence.shape}"
        )

    model = load_model()

    input_tensor = torch.tensor(input_sequence, dtype=torch.float32).unsqueeze(0)  # (1, 4, 3, 56, 96)

    with torch.no_grad():
        output_tensor = model(input_tensor)

    output_array = output_tensor.squeeze(0).cpu().numpy()

    if output_array.shape != (3, 56, 96):
        raise ValueError(
            f"Expected output shape (3, 56, 96), got {output_array.shape}"
        )

    return output_array