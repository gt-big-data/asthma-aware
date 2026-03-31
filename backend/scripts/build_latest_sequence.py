import numpy as np
import rasterio
from sklearn.preprocessing import MinMaxScaler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "raw_rasters"
OUTPUT_DIR = BASE_DIR / "app" / "data"

NDVI_PATH = DATA_DIR / "ndvi_stack.tif"
SO2_PATH = DATA_DIR / "SO2_stack_reprojected_ndvi_dims2.tif"
NO2_PATH = DATA_DIR / "NO2_reprojected_stack.tif"

OUTPUT_PATH = OUTPUT_DIR / "latest_observed_sequence.npy"


def load_raster_stack(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        arr = src.read()
    arr = np.nan_to_num(arr)
    return arr.astype(np.float32)


def minmax_scale_stack(arr: np.ndarray) -> np.ndarray:
    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(arr.reshape(-1, arr.shape[-1])).reshape(arr.shape)
    return scaled.astype(np.float32)


def main():
    ndvi = load_raster_stack(NDVI_PATH)
    so2 = load_raster_stack(SO2_PATH)
    no2 = load_raster_stack(NO2_PATH)

    ndvi = minmax_scale_stack(ndvi)
    so2 = minmax_scale_stack(so2)
    no2 = minmax_scale_stack(no2)

    # Repeat NDVI to daily frequency, matching notebook logic
    ndvi_daily = np.repeat(ndvi, 16, axis=0)[:so2.shape[0]]

    so2_expanded = np.expand_dims(so2, axis=1)
    ndvi_expanded = np.expand_dims(ndvi_daily, axis=1)
    no2_expanded = np.expand_dims(no2, axis=1)

    combined = np.concatenate([so2_expanded, ndvi_expanded, no2_expanded], axis=1)
    # combined shape should be (time, 3, 56, 96) per ML team's final statement

    print("Combined shape:", combined.shape)

    latest_sequence = combined[-4:]  # shape (4, 3, 56, 96)

    if latest_sequence.shape != (4, 3, 56, 96):
        raise ValueError(f"Expected latest sequence shape (4, 3, 56, 96), got {latest_sequence.shape}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    np.save(OUTPUT_PATH, latest_sequence)

    print(f"Saved latest observed sequence to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()