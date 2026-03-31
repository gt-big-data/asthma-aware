# AsthmaAware Backend

This is the FastAPI backend for the AsthmaAware project.

The backend sits between the ML team and the frontend team.

- The **ML/data side** provides the latest observed environmental frames and a trained ConvLSTM model
- The **backend** loads the model, generates forecasts, converts model outputs into frontend-friendly JSON, and exposes API endpoints
- The **frontend** calls those endpoints to render current and forecast heatmaps

---

## What the Backend Does

The backend is responsible for:

- loading the latest observed environmental sequence
- loading the trained ConvLSTM model
- generating 24h / 48h / 72h forecasts
- converting model outputs into map cells with latitude/longitude
- exposing routes for the frontend to consume

For the current version, the backend assumes:

- region = **Atlanta**
- feature order = **SO2, NDVI, NO2**
- latest observed sequence shape = **(4, 3, 56, 96)**
- model output shape = **(3, 56, 96)**

---

## Current ML Setup

### Features
The current model uses 3 features in this exact order:

1. `so2`
2. `ndvi`
3. `no2`

### Input sequence
The model expects the latest observed sequence as a NumPy array of shape:

```text
(4, 3, 56, 96)
````

Meaning:

* 4 previous time steps
* 3 feature channels
* 56 rows
* 96 columns

### Model input tensor

Before inference, backend adds a batch dimension and converts the NumPy array to a PyTorch tensor of shape:

```text
(1, 4, 3, 56, 96)
```

### Model output

The model predicts the next frame with shape:

```text
(3, 56, 96)
```

That output corresponds to the next predicted map frame for:

* SO2
* NDVI
* NO2

### Forecast generation

The model predicts only one next frame at a time.

So the backend generates:

* **24h forecast** = one-step prediction from the latest 4 observed frames
* **48h forecast** = one-step prediction using a sequence that includes the 24h predicted frame
* **72h forecast** = one-step prediction using a sequence that includes the 48h predicted frame

This is a recursive forecasting approach.

---

## Backend Folder Structure

```text
backend/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── constants.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── health.py
│   │   └── map.py
│   │
│   ├── ml/
│   │   ├── base_convlstm.pt
│   │   └── model_definition.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── request_models.py
│   │   └── response_models.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── array_parser.py
│   │   ├── map_service.py
│   │   ├── ml_model_service.py
│   │   ├── forecast_service.py
│   │   └── preprocessing_service.py
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   └── geo.py
│   │
│   └── data/
│       └── latest_observed_sequence.npy
│
├── scripts/
│   └── build_latest_sequence.py
│
├── raw_rasters/
│   ├── ndvi_stack.tif
│   ├── SO2_stack_reprojected_ndvi_dims2.tif
│   └── NO2_reprojected_stack.tif
│
├── tests/
│   ├── test_health.py
│   ├── test_map.py
│   ├── test_ml_model_service.py
│   ├── test_forecast_service.py
│   └── test_array_parser.py
│
├── requirements.txt
├── README.md
└── .gitignore
```

## File and Folder Responsibilities

This section explains what each folder and file is responsible for, how the pieces fit together, and where different kinds of code should live.

---

### `app/`
This is the main backend application folder.

Everything inside `app/` is part of the FastAPI service itself:
- API routes
- ML/model loading
- business logic
- utility functions
- data files used directly by the app

Think of `app/` as the actual backend code that runs when the server starts.

---

### `app/main.py`
This is the entry point of the backend application.

It is responsible for:
- creating the FastAPI app object
- registering the route files
- defining the app title/version through config

When you run:

```bash
uvicorn app.main:app --reload
````

this is the file FastAPI starts from.

**Why it matters:**
Without `main.py`, the backend cannot start and none of the routes exist.

---

### `app/config.py`

This file stores basic backend configuration values.

Typical examples:

* application name
* application version
* default city

This file is useful because it keeps simple app-wide settings in one place instead of scattering them across multiple files.

**Why it matters:**
It makes the project easier to maintain and change later.

---

### `app/constants.py`

This file stores shared constant values that are used in multiple places.

Right now, this includes things like:

* `FEATURE_ORDER`
* Atlanta map bounds
* default city name

Examples:

* feature order must stay consistent between preprocessing, model output parsing, and frontend JSON
* Atlanta bounds must stay consistent when converting rows/cols into latitude/longitude

**Why it matters:**
If these values were hardcoded in several files, it would be easy for the backend to become inconsistent.

---

## `app/api/`

This folder contains the HTTP route files.

These files define the URLs the frontend can call.

The route layer should stay **thin**:

* receive request
* call service layer
* return response

Heavy business logic should not live here.

**Why it matters:**
Keeping routes thin makes the code cleaner and reduces merge conflicts.

---


### `app/api/health.py`

This file defines the health check route:

* `GET /health`

Its job is simple:

* confirm that the backend service is up and reachable

Example response:

```json
{
  "status": "ok"
}
```

**Why it matters:**
This is the fastest way to verify that the backend server is running before testing anything else.

---

### `app/api/map.py`

This file defines the map-related routes, such as:

* `GET /map/current`
* `GET /map/forecast/24h`
* `GET /map/forecast/48h`
* `GET /map/forecast/72h`

This file should not do the ML work itself.
Instead, it calls functions from the service layer.

Its job is:

* define the route paths
* receive query parameters if needed
* call the correct service function
* return the API response

**Why it matters:**
This is the main interface the frontend team uses.

---

## `app/ml/`

This folder stores ML-related artifacts that the backend needs in order to run inference.

It is separate from `services/` because this folder contains the **model artifact and model class definitions**, not the backend orchestration logic.

---

### `app/ml/base_convlstm.pt`

This is the trained PyTorch model file currently used for forecasting.

The backend loads this file during inference.

**Current note:**
This file is a serialized full model object, not just a `state_dict`, which is why the backend needs the model class definitions and a small loading workaround.

**Why it matters:**
Without this file, forecast generation does not work.

---

### `app/ml/model_definition.py`

This file contains the model class definitions required to load the saved model.

It includes:

* `ConvLSTMCell`
* `ConvLSTM`

This file does **not** train the model.
It exists so the backend can reconstruct the same model class when `torch.load(...)` is called.

**Why it matters:**
If the class definitions are missing, PyTorch cannot deserialize the saved model object.

---

## `app/models/`

This folder contains response/request schemas used by the backend.

These models define the structure of the data returned by the API.

This helps with:

* clarity
* validation
* documentation
* consistency between backend and frontend

---

### `app/models/request_models.py`

This file is reserved for structured request body models if needed later.

Right now, the backend mainly uses simple GET routes and query parameters, so this file may be minimal or unused.

It is kept here so the project has a clean place for future request schemas.

---

### `app/models/response_models.py`

This file defines the shape of API responses.

For example:

* a single map cell
* the current map response
* the forecast response

A map cell includes:

* row
* col
* lat
* lon
* so2
* ndvi
* no2

**Why it matters:**
This keeps response shapes explicit and makes it easier for frontend developers to know what to expect.

---

## `app/services/`

This folder contains the main backend logic.

This is the most important folder in the backend because it is where the real work happens.

Service files are responsible for:

* loading data
* loading the model
* generating forecasts
* transforming outputs
* preparing API-ready responses

A simple way to think about this folder is:

* `api/` = doors into the backend
* `services/` = the brains of the backend

---


### `app/services/preprocessing_service.py`

This file is responsible for preprocessing model inputs and postprocessing model outputs.

Right now it uses placeholder logic:

* input sequence passes through unchanged
* output frame passes through unchanged

Later, this file should be updated to include:

* real normalization
* inverse transformation back to original units
* any feature-specific preprocessing needed by the ML model

**Why it matters:**
This file is the correct place for ML preprocessing logic.
That keeps it separate from routes and forecast orchestration.

---

### `app/services/ml_model_service.py`

This file is responsible for:

* loading the trained model
* converting NumPy arrays to PyTorch tensors
* running one-step model inference
* converting the model output back to NumPy

This service handles a **single prediction step** only.

Input:

* NumPy array of shape `(4, 3, 56, 96)`

Output:

* NumPy array of shape `(3, 56, 96)`

**Why it matters:**
This keeps model loading and direct inference isolated in one place.

---

### `app/services/forecast_service.py`

This file handles recursive forecast generation.

Because the model predicts only one next frame at a time, this service is responsible for turning one-step prediction into:

* 24h forecast
* 48h forecast
* 72h forecast

It does this by:

1. taking the latest observed 4-frame sequence
2. predicting the next frame (`24h`)
3. shifting the sequence window and using the prediction to generate `48h`
4. repeating again for `72h`

**Why it matters:**
This file contains the forecasting logic, not the route files and not the model-loading file.

---

### `app/services/array_parser.py`

This file converts model output arrays into frontend-friendly JSON cell objects.

The model output shape is:

```text
(3, 56, 96)
```

But the frontend wants a list of cell objects like:

* row
* col
* lat
* lon
* so2
* ndvi
* no2

So this file loops through every grid location and attaches geographic coordinates and feature values.

**Why it matters:**
This is the translation layer between raw model output and map API responses.

---

### `app/services/map_service.py`

This file coordinates current-map and forecast-map behavior.

It is the high-level service layer that:

* loads the latest observed sequence
* uses the last observed frame for `/map/current`
* calls `forecast_service.py` for forecasts
* caches forecast results in memory
* returns final response dictionaries for routes

This file is effectively the **main map business logic layer**.

**Why it matters:**
It ties together data loading, parsing, forecasting, and API response shaping.

---

## `app/utils/`

This folder contains smaller helper functions that are shared across the backend.

These helpers are usually reusable and not tied to a single route or service.

---

### `app/utils/geo.py`

This file converts `(row, col)` grid coordinates into approximate `(lat, lon)` geographic coordinates using the Atlanta bounding box.

The backend knows:

* total number of rows
* total number of columns
* min/max latitude
* min/max longitude

Using that, it estimates the geographic position of each grid cell.

**Why it matters:**
The model output is grid-based, but the frontend heatmap needs latitude/longitude values.

---

## `app/data/`

This folder stores backend data files that are directly consumed by the application.

Right now the most important file here is:

* `latest_observed_sequence.npy`

This folder is for **app input data**, not raw source raster data.

---

### `app/data/latest_observed_sequence.npy`

This file stores the latest observed 4-frame sequence used for:

* current map display
* forecast generation

Expected shape:

```text
(4, 3, 56, 96)
```

This file is generated from the raster stack preprocessing script.

**Why it matters:**
The model cannot run unless this input sequence exists.

---

## `scripts/`

This folder contains helper scripts used during development or data preparation.

These scripts are not part of the HTTP API itself, but they support backend workflows.

---

### `scripts/build_latest_sequence.py`

This script builds the latest observed sequence file from the raster stacks.

It is responsible for:

* loading NDVI, SO2, and NO2 raster stacks
* applying the same general preprocessing pattern used in the ML notebook
* repeating NDVI across time to align with the other data
* stacking features in the correct order
* extracting the most recent 4 time steps
* saving the result as `app/data/latest_observed_sequence.npy`

**Why it matters:**
This is the bridge between raw raster data and the backend model input.

---

## `raw_rasters/`

This folder stores the raw `.tif` raster files provided by the ML/data team.

These are source files used to build the latest observed sequence.

Examples:

* `ndvi_stack.tif`
* `SO2_stack_reprojected_ndvi_dims2.tif`
* `NO2_reprojected_stack.tif`

These files are not served directly by the backend.
They are only used by the preprocessing script.

**Why it matters:**
Without these files, the backend cannot regenerate the latest observed sequence on its own.

---

## `tests/`

This folder contains automated tests for the backend.

Tests help verify that:

* routes work
* parsing works
* inference works
* recursive forecasting works

This is important because the backend now includes ML inference and multiple moving pieces.

---

### `tests/test_health.py`

Tests the `/health` route.

Goal:

* confirm backend is reachable
* confirm correct JSON response

---

### `tests/test_map.py`

Tests the main map routes:

* `/map/current`
* `/map/forecast/24h`
* `/map/forecast/48h`
* `/map/forecast/72h`

Goal:

* confirm routes return `200`
* confirm expected JSON structure
* confirm cells exist and contain expected keys

---

### `tests/test_ml_model_service.py`

Tests one-step inference.

Goal:

* confirm `predict_next_frame(...)` works
* confirm output shape is `(3, 56, 96)`

This verifies that the model can be loaded and run from backend code.

---

### `tests/test_forecast_service.py`

Tests recursive forecast generation.

Goal:

* confirm 24h / 48h / 72h forecasts are generated
* confirm each forecast has the correct shape

This verifies the multi-step forecasting flow.

---

### `tests/test_array_parser.py`

Tests conversion from model output array to frontend-friendly cells.

Goal:

* confirm parser creates the correct number of cells
* confirm feature values are mapped correctly
* confirm lat/lon fields are included


---

## How the Backend Flow Works

### Current map flow

1. Backend loads `latest_observed_sequence.npy`
2. It takes the last observed frame from the sequence
3. It converts that frame into cells with lat/lon
4. It returns the result from `/map/current`

### Forecast flow

1. Backend loads `latest_observed_sequence.npy`
2. Backend feeds the 4-frame sequence into the ConvLSTM model
3. Model predicts the next frame → 24h forecast
4. Backend recursively uses predicted frames to generate 48h and 72h forecasts
5. Backend converts those outputs into cells with lat/lon
6. It returns the result from the forecast endpoints

---

## API Routes

### Health

**GET** `/health`

Example response:

```json
{
  "status": "ok"
}
```

### Current map

**GET** `/map/current`

Returns the latest observed environmental map.

### Forecast routes

**GET** `/map/forecast/24h`
**GET** `/map/forecast/48h`
**GET** `/map/forecast/72h`

Each returns one forecasted environmental map.

---

## Example Response Shape

Example structure for a map route:

```json
{
  "city": "atlanta",
  "timestamp": "latest_observed_frame",
  "cells": [
    {
      "row": 0,
      "col": 0,
      "lat": 33.89,
      "lon": -84.55,
      "so2": 0.42,
      "ndvi": 0.67,
      "no2": 0.31
    }
  ]
}
```

Forecast routes use a similar structure, with `forecast_horizon` included.

---

## Running the Backend Locally

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Build the latest observed sequence

Make sure the raster files exist in the expected folder, then run:

```bash
python scripts/build_latest_sequence.py
```

This should create:

```text
app/data/latest_observed_sequence.npy
```

### 3. Start the backend

From inside the `backend/` folder:

```bash
uvicorn app.main:app --reload
```

### 4. Open the API

Base URL:

```text
http://127.0.0.1:8000
```

Swagger docs:

```text
http://127.0.0.1:8000/docs
```

---

## Recommended Manual Test Order

1. `GET /health`
2. `GET /map/current`
3. `GET /map/forecast/24h`
4. `GET /map/forecast/48h`
5. `GET /map/forecast/72h`

You can also test model services directly from the terminal if needed.

---

## Backend Team Responsibilities

### API / frontend integration

* maintain routes
* keep response shapes frontend-friendly
* improve docs and Swagger descriptions

### ML inference integration

* maintain model loading and forecast generation
* improve preprocessing/postprocessing when scaler artifacts arrive
* keep inference stable

### Data / testing / docs

* regenerate latest observed sequence when needed
* maintain tests
* keep README and setup instructions updated

---

## Summary

The backend’s job is to:

* load the latest observed environmental sequence
* run ML forecasting
* convert outputs into map cells
* expose simple routes for the frontend
