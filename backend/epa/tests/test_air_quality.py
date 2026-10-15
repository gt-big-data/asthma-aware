"""Tests for the AirNow collector. Standard library only, no network calls.

Run from backend/epa/:
    python3 -m unittest discover tests
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from ingestion.air_quality import airnow_client, collector
from ingestion.air_quality.airnow_client import AirNowError, BoundingBox
from ingestion.air_quality.normalize import normalize_observations

METRO_BBOX = BoundingBox(min_lat=33.30, max_lat=34.10, min_lon=-84.90, max_lon=-83.90)
RETRIEVED_AT = datetime(2026, 9, 15, 17, 19, 26, tzinfo=timezone.utc)

# A real observation from the 2026-09-15 atlanta-metro run.
UNITED_AVE_PM25 = {
    "Latitude": 33.7206,
    "Longitude": -84.3578,
    "UTC": "2026-09-14T17:00",
    "Parameter": "PM2.5",
    "Unit": "UG/M3",
    "Value": 4.4,
    "AQI": 24,
    "Category": 1,
    "SiteName": "United Ave",
    "AgencyName": "Georgia Department of Natural Resources",
    "FullAQSCode": "131210055",
    "IntlAQSCode": "840131210055",
}


def _observation(**overrides):
    return {**UNITED_AVE_PM25, **overrides}


class NormalizeTests(unittest.TestCase):
    def test_maps_airnow_fields_to_schema(self):
        records, stats = normalize_observations([UNITED_AVE_PM25], METRO_BBOX, RETRIEVED_AT)

        self.assertEqual(
            records,
            [
                {
                    "source": "airnow",
                    "station_id": "131210055",
                    "station_name": "United Ave",
                    "latitude": 33.7206,
                    "longitude": -84.3578,
                    "observed_at": "2026-09-14T17:00:00Z",
                    "retrieved_at": "2026-09-15T17:19:26Z",
                    "pollutant": "pm25",
                    "value": 4.4,
                    "unit": "ug/m3",
                    "aqi": 24,
                }
            ],
        )
        self.assertEqual(stats["records_written"], 1)

    def test_ozone_and_no2_codes(self):
        payload = [
            _observation(Parameter="OZONE", Unit="PPB", Value=56.0, AQI=54),
            _observation(Parameter="NO2", Unit="PPB", Value=8.0, AQI=7),
        ]
        records, _ = normalize_observations(payload, METRO_BBOX, RETRIEVED_AT)

        self.assertEqual({(r["pollutant"], r["unit"]) for r in records}, {("o3", "ppb"), ("no2", "ppb")})

    def test_missing_value_becomes_null(self):
        records, _ = normalize_observations([_observation(Value=-999)], METRO_BBOX, RETRIEVED_AT)
        self.assertIsNone(records[0]["value"])
        self.assertEqual(records[0]["aqi"], 24)

    def test_missing_aqi_becomes_null(self):
        records, _ = normalize_observations([_observation(AQI=-999)], METRO_BBOX, RETRIEVED_AT)
        self.assertEqual(records[0]["value"], 4.4)
        self.assertIsNone(records[0]["aqi"])

    def test_dropped_when_value_and_aqi_both_missing(self):
        records, stats = normalize_observations([_observation(Value=-999, AQI=-999)], METRO_BBOX, RETRIEVED_AT)
        self.assertEqual(records, [])
        self.assertEqual(stats["missing_measurement"], 1)

    def test_dropped_when_outside_bounding_box(self):
        records, stats = normalize_observations([_observation(Latitude=35.0)], METRO_BBOX, RETRIEVED_AT)
        self.assertEqual(records, [])
        self.assertEqual(stats["outside_bounding_box"], 1)

    def test_duplicates_collapse_to_last_occurrence(self):
        payload = [_observation(Value=4.4), _observation(Value=5.0)]
        records, stats = normalize_observations(payload, METRO_BBOX, RETRIEVED_AT)

        self.assertEqual([r["value"] for r in records], [5.0])
        self.assertEqual(stats["duplicates_collapsed"], 1)

    def test_station_id_falls_back_to_coordinates(self):
        records, _ = normalize_observations(
            [_observation(FullAQSCode="", IntlAQSCode=None)], METRO_BBOX, RETRIEVED_AT
        )
        self.assertEqual(records[0]["station_id"], "airnow-33.7206--84.3578")


class CollectionWindowTests(unittest.TestCase):
    def test_window_holds_exactly_the_requested_hours(self):
        start, end = collector.collection_window(24, RETRIEVED_AT)

        self.assertEqual(start, datetime(2026, 9, 14, 18, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 15, 17, tzinfo=timezone.utc))
        self.assertEqual((end - start).total_seconds() / 3600 + 1, 24)

    def test_one_hour_window_is_current_hour(self):
        start, end = collector.collection_window(1, RETRIEVED_AT)
        self.assertEqual(start, end)


class LoadRegionTests(unittest.TestCase):
    def _write(self, directory: str, spec: dict) -> Path:
        path = Path(directory) / "region.json"
        path.write_text(json.dumps(spec))
        return path

    def test_loads_shipped_region_file(self):
        region, bbox = collector.load_region(collector.PROJECT_ROOT / "regions" / "atlanta-metro.json")
        self.assertEqual(region, "atlanta-metro")
        self.assertLess(bbox.min_lat, bbox.max_lat)

    def test_default_region_file_is_the_shipped_one(self):
        self.assertEqual(collector.DEFAULT_REGION_FILE, collector.PROJECT_ROOT / "regions" / "atlanta-metro.json")

    def test_rejects_missing_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, {"region": "x", "min_lat": 33, "max_lat": 34, "min_lon": -85})
            with self.assertRaises(ValueError):
                collector.load_region(path)

    def test_rejects_inverted_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, {"region": "x", "min_lat": 34, "max_lat": 33, "min_lon": -85, "max_lon": -84})
            with self.assertRaises(ValueError):
                collector.load_region(path)


class ClientTests(unittest.TestCase):
    def test_request_uses_lon_lat_bbox_and_redacts_key(self):
        start = datetime(2026, 9, 14, 18, tzinfo=timezone.utc)
        end = datetime(2026, 9, 15, 17, tzinfo=timezone.utc)
        with mock.patch.object(airnow_client, "_get_with_retries", return_value=b"[]") as get:
            body, meta = airnow_client.fetch_observations("secret-key", METRO_BBOX, start, end)

        url = get.call_args.args[0]
        self.assertIn("BBOX=-84.9%2C33.3%2C-83.9%2C34.1", url)
        self.assertIn("startDate=2026-09-14T18", url)
        self.assertIn("endDate=2026-09-15T17", url)
        self.assertEqual(body, b"[]")
        self.assertNotIn("API_KEY", meta["query"])
        self.assertNotIn("secret-key", json.dumps(meta))

    def test_service_error_payload_raises(self):
        error_body = json.dumps({"WebServiceError": [{"Message": "Invalid API key"}]}).encode()
        with mock.patch.object(airnow_client, "_get_with_retries", return_value=error_body):
            with self.assertRaises(AirNowError):
                airnow_client.fetch_observations("bad", METRO_BBOX, RETRIEVED_AT, RETRIEVED_AT)


class CollectorEndToEndTests(unittest.TestCase):
    def test_writes_raw_meta_and_normalized_files(self):
        raw_body = json.dumps(
            [UNITED_AVE_PM25, _observation(Parameter="OZONE", Unit="PPB", Value=56.0, AQI=54)]
        ).encode()
        request_meta = {"endpoint": airnow_client.API_URL, "query": {"BBOX": METRO_BBOX.as_param()}}

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            collector, "fetch_observations", return_value=(raw_body, request_meta)
        ), contextlib.redirect_stdout(io.StringIO()):
            exit_code = collector.main(
                [
                    "--hours", "24",
                    "--region-file", str(collector.PROJECT_ROOT / "regions" / "atlanta-metro.json"),
                    "--data-dir", directory,
                    "--api-key", "secret-key",
                ]
            )
            data_dir = Path(directory)
            raw_files = sorted((data_dir / collector.RAW_SUBDIR).glob("*.json"))
            normalized_files = list((data_dir / collector.NORMALIZED_SUBDIR).glob("*.json"))

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(raw_files), 2)  # response + .meta.json
            raw_file = next(f for f in raw_files if not f.name.endswith(".meta.json"))
            meta_file = next(f for f in raw_files if f.name.endswith(".meta.json"))
            self.assertTrue(raw_file.name.startswith("atlanta-metro_"))
            self.assertEqual(raw_file.read_bytes(), raw_body)
            self.assertNotIn("secret-key", meta_file.read_text())

            self.assertEqual(len(normalized_files), 1)
            records = json.loads(normalized_files[0].read_text())
            self.assertEqual(sorted(r["pollutant"] for r in records), ["o3", "pm25"])

    def test_missing_api_key_exits_with_code_2(self):
        with mock.patch.dict("os.environ", {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(collector.main([]), 2)


if __name__ == "__main__":
    unittest.main()
