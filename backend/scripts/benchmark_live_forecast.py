"""Offline development microbenchmark; representative fixture, no network/DB."""
import json
import time
import tracemalloc
from datetime import datetime, timedelta, timezone

from app.services.live_forecast import Snapshot, at_location, native_grid, UNITS


def main():
    initialized = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)
    hours = [{"timestamp": (initialized + timedelta(hours=i)).isoformat(),
              "values": {"pm25": 25.0, "no2": 50.0, "o3": 50.0}} for i in range(48)]
    payload = {"schema_version": "nasa-placeholder-v1", "source": "NASA GEOS-CF v2",
               "initialized_at": initialized.isoformat(), "run_id": "20261115_09z", "units": UNITS,
               "cells": [{"lat": 30.25 + row * .25, "lon": -85.5 + col * .25, "hours": hours}
                         for row in range(20) for col in range(20)]}
    raw = json.dumps(payload).encode()
    # Decode independent per-cell records, matching actual JSON memory shape.
    decoded = json.loads(raw)
    tracemalloc.start()
    started = time.perf_counter()
    snapshot = Snapshot.build(decoded)
    build_ms = (time.perf_counter() - started) * 1000
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    started = time.perf_counter()
    for _ in range(1000):
        at_location(snapshot, 33.75, -84.39, initialized + timedelta(hours=3, minutes=20))
    request_ms = (time.perf_counter() - started)  # 1000 requests → numeric ms/request
    grid = native_grid(33.75, -84.5)
    print(json.dumps({"native_cells": 400, "hours": 48, "artifact_bytes": len(raw),
                      "snapshot_build_ms_with_tracemalloc": round(build_ms, 2),
                      "snapshot_extra_peak_bytes": peak,
                      "cached_lookup_ms_mean_1000": round(request_ms, 4),
                      "display_pixels_selected_hour": grid["rows"] * grid["cols"],
                      "avoided_full_horizon_pixel_records": grid["rows"] * grid["cols"] * 400 * 48}, indent=2))


if __name__ == "__main__":
    main()
