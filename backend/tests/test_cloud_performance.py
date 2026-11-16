"""Measured local fixture lookup cost, not a network/cloud SLA benchmark."""
import json
import statistics
import time
import tracemalloc

from forecast_pipeline.catalog import ForecastCatalog
from forecast_pipeline.storage import LocalArtifactStore
from tests.test_cloud_api import fixture_manifest, NOW


def test_local_catalog_lookup_memory_and_latency(tmp_path, capsys):
    store = LocalArtifactStore(tmp_path)
    fixture_manifest(store, "processed")
    catalog = ForecastCatalog(store)
    tracemalloc.start()
    durations = []
    for _ in range(100):
        start = time.perf_counter()
        result = catalog.metadata("processed", NOW)
        durations.append((time.perf_counter() - start) * 1000)
        assert result["product"] == "processed"
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    measurements = {"samples": 100, "local_catalog_ms_median": round(statistics.median(durations), 3),
                    "local_catalog_ms_p95": round(sorted(durations)[94], 3), "lookup_traced_peak_bytes": peak}
    # Bound the fixture's allocations; do not assert a host-dependent time SLA.
    assert peak < 16 * 1024 * 1024
    assert len(durations) == 100
    with capsys.disabled():
        print("CLOUD_CATALOG_BENCHMARK " + json.dumps(measurements))
