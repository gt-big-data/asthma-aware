"""End-to-end tests against a real Postgres + PostGIS.

Skipped unless ``TEST_DATABASE_URL`` points at a throwaway database --
see tests/conftest.py. Run them before merging schema changes; the
no-database tests cannot catch a broken constraint, index or upsert.

    export TEST_DATABASE_URL='postgresql+psycopg://user:pass@localhost:5432/asthma_test'
    pytest tests/test_db_integration.py -v
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tests.conftest import requires_db  # noqa: E402

pytestmark = requires_db

DAY = dt.date(2026, 3, 1)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


def test_grid_cells_are_seeded_and_ordered(session, region):
    from db.repositories import reference

    mapping = reference.get_cell_id_map(session, region)
    assert len(mapping) == region.grid_rows * region.grid_cols

    ordered = reference.get_ordered_cell_ids(session, region)
    assert len(ordered) == region.grid_rows * region.grid_cols
    # Row-major, matching numpy ravel order.
    assert ordered[0] == mapping[(0, 0)]
    assert ordered[1] == mapping[(0, 1)]
    assert ordered[region.grid_cols] == mapping[(1, 0)]


def test_unknown_reference_mentions_the_seed_script(session):
    from db.repositories import reference

    with pytest.raises(reference.UnknownReference, match="seed_reference_data"):
        reference.get_region(session, "atlantis")


# ---------------------------------------------------------------------------
# Grid observations
# ---------------------------------------------------------------------------


def test_frame_round_trips(session, region, variables):
    from db.repositories import grid

    so2 = variables[0]
    rng = np.random.default_rng(0)
    original = rng.random((region.grid_rows, region.grid_cols)).astype(np.float32)

    written = grid.upsert_frame(session, region, so2, DAY, original)
    assert written == region.grid_rows * region.grid_cols

    loaded = grid.load_frame(session, region, so2, DAY)
    np.testing.assert_allclose(loaded, original, rtol=1e-6)


def test_frame_orientation_survives_the_round_trip(session, region, variables):
    """A frame must not come back transposed or flipped.

    Uses a value that encodes its own position, so any reordering shows up
    rather than averaging out.
    """
    from db.repositories import grid

    so2 = variables[0]
    original = np.arange(
        region.grid_rows * region.grid_cols, dtype=np.float32
    ).reshape(region.grid_rows, region.grid_cols)

    grid.upsert_frame(session, region, so2, DAY, original)
    loaded = grid.load_frame(session, region, so2, DAY)

    np.testing.assert_array_equal(loaded, original)
    assert loaded[0, 0] == 0.0
    assert loaded[0, 1] == 1.0
    assert loaded[1, 0] == float(region.grid_cols)


def test_upsert_is_idempotent_and_overwrites(session, region, variables):
    """Re-running a collector must update, not duplicate."""
    from sqlalchemy import func, select

    from db.models import GridObservation
    from db.repositories import grid

    so2 = variables[0]
    first = np.full((region.grid_rows, region.grid_cols), 1.0, dtype=np.float32)
    second = np.full((region.grid_rows, region.grid_cols), 2.0, dtype=np.float32)

    grid.upsert_frame(session, region, so2, DAY, first)
    grid.upsert_frame(session, region, so2, DAY, second)

    count = session.scalar(select(func.count(GridObservation.id)))
    assert count == region.grid_rows * region.grid_cols

    np.testing.assert_allclose(grid.load_frame(session, region, so2, DAY), 2.0)


def test_nan_becomes_null_not_zero(session, region, variables):
    """The core no-data contract.

    A masked pixel stored as 0.0 silently drags down every mean and
    scaler fit. It must come back as NaN and be excluded from aggregates.
    """
    from db.repositories import grid

    so2 = variables[0]
    values = np.full((region.grid_rows, region.grid_cols), 5.0, dtype=np.float32)
    values[0, 0] = np.nan

    grid.upsert_frame(session, region, so2, DAY, values)
    loaded = grid.load_frame(session, region, so2, DAY)

    assert np.isnan(loaded[0, 0])
    assert loaded[0, 1] == pytest.approx(5.0)

    vmin, vmax, count = grid.value_bounds(session, region, so2, )
    assert count == region.grid_rows * region.grid_cols - 1
    assert vmin == pytest.approx(5.0)
    assert vmax == pytest.approx(5.0)


def test_sentinel_nodata_is_converted_to_null(session, region, variables):
    """-9999 from the satellite export must not be stored as a reading."""
    from db.repositories import grid

    so2 = variables[0]
    values = np.full((region.grid_rows, region.grid_cols), 5.0, dtype=np.float32)
    values[0, 0] = -9999.0

    grid.upsert_frame(session, region, so2, DAY, values, nodata_value=-9999.0)
    loaded = grid.load_frame(session, region, so2, DAY)

    assert np.isnan(loaded[0, 0])
    vmin, _vmax, _count = grid.value_bounds(session, region, so2)
    assert vmin == pytest.approx(5.0), "sentinel leaked into the value range"


def test_load_frame_returns_none_for_absent_date(session, region, variables):
    from db.repositories import grid

    assert grid.load_frame(session, region, variables[0], DAY) is None


def test_upsert_rejects_wrong_shape(session, region, variables):
    from db.repositories import grid

    with pytest.raises(ValueError, match="Expected array of shape"):
        grid.upsert_frame(
            session, region, variables[0], DAY, np.zeros((3, 3), dtype=np.float32)
        )


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------


def _write_days(session, region, variables, days: int, start: dt.date = DAY):
    from db.repositories import grid

    for offset in range(days):
        day = start + dt.timedelta(days=offset)
        for channel, variable in enumerate(variables):
            frame = np.full(
                (region.grid_rows, region.grid_cols),
                float(offset * 10 + channel),
                dtype=np.float32,
            )
            grid.upsert_frame(session, region, variable, day, frame)


def test_load_sequence_shape_and_channel_order(session, region, variables):
    from db.repositories import grid

    _write_days(session, region, variables, 4)
    dates = [DAY + dt.timedelta(days=i) for i in range(4)]

    sequence = grid.load_sequence(session, region, variables, dates)

    assert sequence.shape == (4, 3, region.grid_rows, region.grid_cols)
    # Encoded as offset*10 + channel, so this pins both axes at once.
    assert sequence[0, 0, 0, 0] == pytest.approx(0.0)
    assert sequence[0, 2, 0, 0] == pytest.approx(2.0)
    assert sequence[3, 0, 0, 0] == pytest.approx(30.0)
    assert sequence[3, 2, 0, 0] == pytest.approx(32.0)


def test_load_sequence_reorders_with_the_variable_list(session, region, variables):
    """Channel order follows the argument, not the database's row order."""
    from db.repositories import grid

    _write_days(session, region, variables, 1)
    reversed_vars = list(reversed(variables))
    sequence = grid.load_sequence(session, region, reversed_vars, [DAY])

    assert sequence[0, 0, 0, 0] == pytest.approx(2.0)  # no2 was channel 2
    assert sequence[0, 2, 0, 0] == pytest.approx(0.0)  # so2 was channel 0


def test_missing_date_becomes_an_all_nan_plane(session, region, variables):
    from db.repositories import grid

    _write_days(session, region, variables, 1)
    dates = [DAY, DAY + dt.timedelta(days=1)]
    sequence = grid.load_sequence(session, region, variables, dates)

    assert np.isfinite(sequence[0]).all()
    assert np.isnan(sequence[1]).all()


def test_complete_dates_requires_every_variable(session, region, variables):
    from db.repositories import grid

    _write_days(session, region, variables, 2)
    # A third day with only one of the three channels present.
    third = DAY + dt.timedelta(days=2)
    grid.upsert_frame(
        session,
        region,
        variables[0],
        third,
        np.ones((region.grid_rows, region.grid_cols), dtype=np.float32),
    )

    complete = grid.complete_dates(session, region, variables)
    assert third not in complete
    assert len(complete) == 2


def test_latest_complete_dates_rejects_non_consecutive(session, region, variables):
    """A gap must fail loudly rather than produce a bogus forecast."""
    from db.repositories import grid

    _write_days(session, region, variables, 3)
    _write_days(session, region, variables, 1, start=DAY + dt.timedelta(days=10))

    with pytest.raises(ValueError, match="not consecutive"):
        grid.latest_complete_dates(
            session, region, variables, 4, require_consecutive=True
        )

    # Without the flag the same call succeeds.
    dates = grid.latest_complete_dates(
        session, region, variables, 4, require_consecutive=False
    )
    assert len(dates) == 4


def test_latest_complete_dates_reports_insufficient_history(session, region, variables):
    from db.repositories import grid

    _write_days(session, region, variables, 2)
    with pytest.raises(ValueError, match="only 2 exist"):
        grid.latest_complete_dates(session, region, variables, 4)


def test_min_coverage_excludes_heavily_masked_frames(session, region, variables):
    from db.repositories import grid

    total = region.grid_rows * region.grid_cols
    mostly_nan = np.full((region.grid_rows, region.grid_cols), np.nan, dtype=np.float32)
    mostly_nan.ravel()[: total // 4] = 1.0  # 25% coverage

    for variable in variables:
        grid.upsert_frame(session, region, variable, DAY, mostly_nan)

    assert grid.complete_dates(session, region, variables, min_coverage=0.5) == []
    assert grid.complete_dates(session, region, variables, min_coverage=0.2) == [DAY]


# ---------------------------------------------------------------------------
# Scalers
# ---------------------------------------------------------------------------


def test_fit_minmax_uses_sql_aggregate_and_ignores_nulls(session, region, variables):
    from db.repositories import scalers

    from db.repositories import grid

    so2 = variables[0]
    values = np.full((region.grid_rows, region.grid_cols), 4.0, dtype=np.float32)
    values[0, 0] = np.nan
    values[0, 1] = 10.0
    values[0, 2] = 1.0
    grid.upsert_frame(session, region, so2, DAY, values)

    params = scalers.fit_minmax(session, region, so2, version="v1", activate=True)
    assert params.fit_min == pytest.approx(1.0)
    assert params.fit_max == pytest.approx(10.0)
    assert params.sample_count == region.grid_rows * region.grid_cols - 1

    active = scalers.get_active(session, region, so2)
    assert active.version == "v1"
    assert active.transform(np.array([1.0]))[0] == pytest.approx(0.0)
    assert active.transform(np.array([10.0]))[0] == pytest.approx(1.0)


def test_activating_a_version_deactivates_the_previous(session, region, variables):
    from db.repositories import grid, scalers

    so2 = variables[0]
    grid.upsert_frame(
        session,
        region,
        so2,
        DAY,
        np.full((region.grid_rows, region.grid_cols), 3.0, dtype=np.float32),
    )

    scalers.fit_minmax(session, region, so2, version="v1", activate=True)
    scalers.fit_minmax(session, region, so2, version="v2", activate=True)

    assert scalers.get_active(session, region, so2).version == "v2"


def test_scaler_missing_raises_with_instructions(session, region, variables):
    from db.repositories import scalers

    with pytest.raises(scalers.ScalerNotFound, match="fit_scalers.py"):
        scalers.get_active(session, region, variables[0])


def test_stored_values_are_raw_so_rescaling_is_stable(session, region, variables):
    """The whole point: new extremes must not shift historical values.

    Under the old fit-every-run behaviour, adding a larger observation
    silently rescaled all prior data. Here the stored values never move
    and the old scaler keeps producing the old numbers.
    """
    from db.repositories import grid, scalers

    so2 = variables[0]
    grid.upsert_frame(
        session,
        region,
        so2,
        DAY,
        np.full((region.grid_rows, region.grid_cols), 5.0, dtype=np.float32),
    )
    grid.upsert_frame(
        session,
        region,
        so2,
        DAY + dt.timedelta(days=1),
        np.full((region.grid_rows, region.grid_cols), 10.0, dtype=np.float32),
    )
    scalers.fit_minmax(session, region, so2, version="v1", activate=True)
    v1 = scalers.get_active(session, region, so2)
    before = v1.transform(np.array([5.0]))[0]

    # A new, much larger observation arrives.
    grid.upsert_frame(
        session,
        region,
        so2,
        DAY + dt.timedelta(days=2),
        np.full((region.grid_rows, region.grid_cols), 1000.0, dtype=np.float32),
    )

    # Raw storage is untouched, and the active scaler is unchanged.
    np.testing.assert_allclose(
        grid.load_frame(session, region, so2, DAY), 5.0, rtol=1e-6
    )
    still = scalers.get_active(session, region, so2)
    assert still.version == "v1"
    assert still.transform(np.array([5.0]))[0] == pytest.approx(before)


# ---------------------------------------------------------------------------
# Ingestion bookkeeping
# ---------------------------------------------------------------------------


def test_run_scope_marks_success(session, region):
    from db.repositories import ingestion

    with ingestion.run_scope(session, "test", region=region) as run:
        run.rows_written = 5

    assert run.status == "success"
    assert run.finished_at is not None


def test_run_with_issues_is_partial_not_success(session, region, variables):
    from db.repositories import ingestion

    with ingestion.run_scope(session, "test", region=region) as run:
        ingestion.record_issue(
            session, run, "no overpass", variable=variables[0], observation_date=DAY
        )

    assert run.status == "partial"


def test_run_scope_records_failure_and_reraises(session, region):
    from db.repositories import ingestion

    with pytest.raises(RuntimeError, match="upstream exploded"):
        with ingestion.run_scope(session, "test", region=region) as run:
            raise RuntimeError("upstream exploded")

    assert run.status == "failed"
    assert "upstream exploded" in run.error


def test_raster_file_provenance_upserts(session, region, variables):
    from db.repositories import ingestion

    ndvi = variables[1]
    with ingestion.run_scope(session, "satellite", region=region) as run:
        first = ingestion.record_raster_file(
            session,
            region=region,
            variable=ndvi,
            observation_date=DAY,
            source_date=DAY - dt.timedelta(days=11),
            path="data/satellite/2026-03-01/ndvi.tif",
            run=run,
            unit="ndvi",
            crs="EPSG:4326",
            qa_filtering="SummaryQA <= 1",
        )
        # NDVI composites: the acquisition date legitimately differs.
        assert first.source_date != first.observation_date

        again = ingestion.record_raster_file(
            session,
            region=region,
            variable=ndvi,
            observation_date=DAY,
            path="data/satellite/2026-03-01/ndvi.tif",
            run=run,
            unit="ndvi",
        )
        assert again.id == first.id, "should update in place, not duplicate"


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------


def test_forecast_frame_round_trips(session, region, variables):
    from db.repositories import forecasts

    run = forecasts.create_model_run(
        session,
        region=region,
        model_name="base_convlstm",
        feature_order=[v.slug for v in variables],
        scaler_version="v1",
    )
    frame = np.arange(
        len(variables) * region.grid_rows * region.grid_cols, dtype=np.float32
    ).reshape(len(variables), region.grid_rows, region.grid_cols)

    forecasts.save_forecast_frame(
        session,
        model_run=run,
        region=region,
        variables=variables,
        horizon="24h",
        frame=frame,
    )

    loaded = forecasts.load_forecast_frame(
        session, model_run=run, region=region, variables=variables, horizon="24h"
    )
    np.testing.assert_allclose(loaded, frame, rtol=1e-6)


def test_forecast_cells_match_the_api_response_shape(session, region, variables):
    from db.repositories import forecasts

    run = forecasts.create_model_run(
        session, region=region, model_name="base_convlstm"
    )
    frame = np.ones(
        (len(variables), region.grid_rows, region.grid_cols), dtype=np.float32
    )
    forecasts.save_forecast_frame(
        session,
        model_run=run,
        region=region,
        variables=variables,
        horizon="24h",
        frame=frame,
    )

    cells = forecasts.load_forecast_cells(
        session, model_run=run, region=region, variables=variables, horizon="24h"
    )
    assert len(cells) == region.grid_rows * region.grid_cols
    first = cells[0]
    # Same keys app/services/array_parser.py emits today.
    for key in ("row", "col", "lat", "lon", "so2", "ndvi", "no2"):
        assert key in first
    assert first["row"] == 0 and first["col"] == 0
    assert first["lat"] == pytest.approx(region.max_lat)


# ---------------------------------------------------------------------------
# Stations and socioeconomic
# ---------------------------------------------------------------------------


def test_station_observations_upsert_and_require_tz(session, region):
    from db.repositories import reference, stations

    source = reference.upsert_data_source(session, "airnow", name="AirNow")
    station = stations.upsert_station(
        session,
        source=source,
        external_id="ATL-001",
        lat=33.75,
        lon=-84.39,
        name="Midtown",
        region=region,
    )
    pm25 = reference.upsert_variable(
        session, "pm25", display_name="PM2.5", kind="ground", canonical_unit="ug/m^3"
    )

    when = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)
    written = stations.upsert_observations(
        session,
        [{"station_id": station.id, "variable_id": pm25.id, "observed_at": when, "value": 12.5}],
    )
    assert written == 1

    # Re-ingesting an overlapping window updates rather than erroring.
    stations.upsert_observations(
        session,
        [{"station_id": station.id, "variable_id": pm25.id, "observed_at": when, "value": 13.0}],
    )
    window = stations.observation_window(session, station, pm25)
    assert window is not None

    with pytest.raises(ValueError, match="timezone-aware"):
        stations.upsert_observations(
            session,
            [{
                "station_id": station.id,
                "variable_id": pm25.id,
                "observed_at": dt.datetime(2026, 3, 1, 12, 0),
                "value": 1.0,
            }],
        )


def test_socioeconomic_record_round_trips(session, region):
    from db.repositories import socioeconomic as socio

    zcta = socio.upsert_zcta(session, "30308", region=region, area_sq_miles=1.89)
    socio.upsert_record(
        session,
        zcta=zcta,
        dataset_year=2023,
        source="us_census_acs5_2023",
        indicators={
            "zipcode": "30308",
            "area_sq_miles": 1.89,
            "population": 12345,
            "poverty_rate": 14.2,
            "median_household_income": None,
        },
        raw={"B01001_001E": "12345"},
    )

    records = socio.get_records(session, region=region)
    assert len(records) == 1
    payload = socio.record_to_dict(records[0])
    assert payload["zipcode"] == "30308"
    assert payload["population"] == 12345
    assert payload["poverty_rate"] == pytest.approx(14.2)
    # A missing ACS value stays None rather than becoming 0.
    assert payload["median_household_income"] is None
