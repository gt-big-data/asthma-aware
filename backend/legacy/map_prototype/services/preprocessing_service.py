import numpy as np


EXPECTED_SEQUENCE_SHAPE = (4, 3, 56, 96)
EXPECTED_FRAME_SHAPE = (3, 56, 96)


def preprocess_input_sequence(sequence: np.ndarray) -> np.ndarray:
    """
    Temporary placeholder preprocessing.
    Later this is where real scaler logic should go.
    """
    if sequence.shape != EXPECTED_SEQUENCE_SHAPE:
        raise ValueError(
            f"Expected input sequence shape {EXPECTED_SEQUENCE_SHAPE}, got {sequence.shape}"
        )

    return sequence.astype(np.float32)


def postprocess_output_frame(frame: np.ndarray) -> np.ndarray:
    """
    Temporary placeholder inverse transform.
    Later this is where inverse scaling should go.
    """
    if frame.shape != EXPECTED_FRAME_SHAPE:
        raise ValueError(
            f"Expected output frame shape {EXPECTED_FRAME_SHAPE}, got {frame.shape}"
        )

    return frame.astype(np.float32)