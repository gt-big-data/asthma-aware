
# AsthmaAware Backend

This is the FastAPI backend for the AsthmaAware project.

The backend acts as the layer between the ML team and the frontend team.

- The **ML team** provides environmental grid data in a fixed array format
- The **backend** reads that data, validates it, converts it into frontend-friendly JSON, and exposes API endpoints
- The **frontend team** calls those endpoints to display current and forecast heatmaps

---

# Purpose of the Backend

The backend is responsible for:

- loading raw environmental grid data
- validating the structure of that data
- converting grid cells into latitude/longitude coordinates
- exposing API routes for current and forecast map data
- giving the frontend data in a clean JSON format

For the current demo, the backend assumes:

- the region is **Atlanta**
- the ML grid format is fixed
- the backend uses mock/local JSON files as stand-ins for ML output

---

# Current Data Format

The current agreed ML-to-backend format is:

```text
grid[rows][cols][5]
````

Each cell contains values in this exact order:

```text
[so2, no2, ozone, ndvi, pollen]
```

### Feature meanings

* `so2` = sulfur dioxide
* `no2` = nitrogen dioxide
* `ozone` = ground-level ozone
* `ndvi` = vegetation / greenness index
* `pollen` = pollen intensity

The backend uses the row and column position of each cell to compute approximate latitude and longitude values within the Atlanta bounding box.

---

# Backend Folder Structure

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
│   ├── models/
│   │   ├── __init__.py
│   │   ├── request_models.py
│   │   └── response_models.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── array_parser.py
│   │   ├── map_service.py
│   │   └── mock_data_service.py
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── geo.py
│   │   └── validators.py
│   │
│   └── data/
│       ├── current_grid.json
│       ├── forecast_24h.json
│       ├── forecast_48h.json
│       └── forecast_72h.json
│
├── tests/
│   ├── test_health.py
│   ├── test_map.py
│   └── test_array_parser.py
│
├── requirements.txt
├── README.md
└── .gitignore
```

---

# What Each File/Folder Does

## `app/main.py`

This is the entry point of the backend.

It creates the FastAPI application and connects the route files.

Without this file, the backend will not start.

---

## `app/config.py`

Stores basic application settings such as:

* app name
* version
* default values

This is where general backend configuration goes.

---

## `app/constants.py`

Stores fixed values that are shared across the project, such as:

* feature order
* Atlanta bounding box
* default city

This keeps important assumptions in one place instead of hardcoding them everywhere.

---

## `app/api/`

This folder contains the route files.

These files define the API endpoints that the frontend can call.

### `health.py`

Contains a simple health check route:

* `GET /health`

This is mainly used to confirm that the backend is running.

### `map.py`

Contains the map-related routes:

* `GET /map/current`
* `GET /map/forecast/24h`
* `GET /map/forecast/48h`
* `GET /map/forecast/72h`

These routes call the service layer and return JSON responses.

---

## `app/models/`

This folder contains data models / schemas.

These help define the structure of request and response data.

### `request_models.py`

Used for request body models if needed later.

Right now the backend mostly uses query parameters, so this file is minimal.

### `response_models.py`

Defines the structure of the JSON returned by the backend, such as:

* one map cell
* current map response
* forecast response

---

## `app/services/`

This folder contains the main backend logic.

### `array_parser.py`

This is one of the most important files.

It takes the raw ML grid array and converts it into structured cell objects that include:

* row
* col
* lat
* lon
* so2
* no2
* ozone
* ndvi
* pollen

### `map_service.py`

This file coordinates the main map logic.

It:

* loads the raw data
* sends the grid to the parser
* returns the final JSON response for the API

### `mock_data_service.py`

This file loads local JSON files from the `data/` folder.

For now, these JSON files act like mock ML outputs so the backend and frontend can keep developing before full ML integration.

---

## `app/utils/`

This folder contains helper functions.

### `geo.py`

Contains logic to convert grid row/column positions into latitude/longitude coordinates inside the Atlanta region.

### `validators.py`

Contains checks to make sure the grid shape is valid before the backend tries to parse it.

For example, it checks:

* grid is not empty
* all rows have the same number of columns
* each cell has exactly 5 values
* values are numeric

---

## `app/data/`

This folder stores the current mock/demo datasets.

### Files

* `current_grid.json`
* `forecast_24h.json`
* `forecast_48h.json`
* `forecast_72h.json`

These files are the backend input data for now.

They simulate what the ML team would eventually provide.

---

## `tests/`

This folder contains backend tests.

### `test_health.py`

Checks that the health endpoint works.

### `test_map.py`

Checks that map routes return valid responses.

### `test_array_parser.py`

Checks that the raw grid is being converted correctly into structured cells.

---

# How the Backend Flow Works

Here is the basic flow when the frontend calls the backend:

1. The frontend sends a request to a route like `/map/current`
2. The route in `app/api/map.py` receives the request
3. The route calls a function in `app/services/map_service.py`
4. That service loads raw grid data from `app/data/`
5. The service sends the grid to `array_parser.py`
6. The parser validates the grid and loops through each cell
7. The parser uses `geo.py` to compute lat/lon for each cell
8. The parser returns structured JSON-ready data
9. The route returns that data to the frontend

---

# Current API Routes

## Health

* `GET /health`

Example response:

```json
{
  "status": "ok"
}
```

## Current map

* `GET /map/current`

Returns the current environmental grid for Atlanta.

## Forecast routes

* `GET /map/forecast/24h`
* `GET /map/forecast/48h`
* `GET /map/forecast/72h`

Each route returns one forecast snapshot.

---

# Running the Backend Locally

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

## 2. Start the server

From inside the `backend/` folder:

```bash
uvicorn app.main:app --reload
```

## 3. Open in browser

Backend base URL:

```text
http://127.0.0.1:8000
```

Swagger docs:

```text
http://127.0.0.1:8000/docs
```