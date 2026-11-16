import type { Coordinates } from "./logging";

export interface ForecastHour {
  timestamp: string;
  values: { pm25: number; no2: number; o3: number };
  score: number;
}
export interface LiveForecast {
  source: string;
  run_id: string;
  initialized_at: string;
  valid_until: string;
  method: string;
  placeholder: true;
  notice: string;
  location: {
    lat: number; lon: number; pixel_id: string;
    native_lat: number; native_lon: number; display_resolution_km: number;
  };
  units: { pm25: string; no2: string; o3: string };
  grid: { south: number; north: number; west: number; east: number;
    rows: number; cols: number; lat_step: number; lon_step: number };
  hourly: ForecastHour[];
}

/** Reject incomplete/malformed live responses rather than displaying invented zeros. */
export function parseForecast(value: unknown): LiveForecast {
  const data = value as LiveForecast;
  const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
  if (!data || data.source !== "NASA GEOS-CF v2" || data.placeholder !== true
    || typeof data.run_id !== "string" || !Number.isFinite(Date.parse(data.initialized_at))
    || !Number.isFinite(Date.parse(data.valid_until))
    || !data.location || !finite(data.location.lat) || !finite(data.location.lon)
    || !finite(data.location.native_lat) || !finite(data.location.native_lon)
    || typeof data.notice !== "string" || data.method !== "native-cell-copy; equal-weight-v1"
    || !data.grid || !Number.isInteger(data.grid.rows) || !Number.isInteger(data.grid.cols)
    || data.grid.rows < 1 || data.grid.cols < 1 || data.grid.rows * data.grid.cols > 1000
    || ![data.grid.south, data.grid.north, data.grid.west, data.grid.east, data.grid.lat_step, data.grid.lon_step].every(finite)
    || data.grid.lat_step <= 0 || data.grid.lon_step <= 0
    || data.grid.north <= data.grid.south || data.grid.east <= data.grid.west
    || Math.abs(data.grid.rows * data.grid.lat_step - (data.grid.north - data.grid.south)) > 1e-8
    || Math.abs(data.grid.cols * data.grid.lon_step - (data.grid.east - data.grid.west)) > 1e-8
    || data.units?.pm25 !== "ug m-3" || data.units?.no2 !== "ppb" || data.units?.o3 !== "ppb"
    || !Array.isArray(data.hourly) || data.hourly.length < 1 || data.hourly.length > 120) {
    throw new Error("The forecast response is incomplete. Please try again later.");
  }
  for (let i = 0; i < data.hourly.length; i += 1) {
    const hour = data.hourly[i];
    if (!hour || !finite(hour.score) || hour.score < 0 || hour.score > 100
      || !Number.isFinite(Date.parse(hour.timestamp)) || !hour.values
      || ![hour.values.pm25, hour.values.no2, hour.values.o3].every((v) => finite(v) && v >= 0)
      || (i > 0 && Date.parse(hour.timestamp) - Date.parse(data.hourly[i - 1].timestamp) !== 3600000)) {
      throw new Error("The forecast contains missing or invalid hourly data.");
    }
  }
  if (Date.parse(data.valid_until) !== Date.parse(data.hourly.at(-1)!.timestamp) + 3600000) {
    throw new Error("The forecast has an invalid expiry time.");
  }
  return data;
}

export function forecastHours(data: LiveForecast, now = Date.now()): ForecastHour[] {
  const age = now - Date.parse(data.initialized_at);
  if (age < 0 || age > 48 * 3600000 || now >= Date.parse(data.valid_until)) return [];
  const hours = data.hourly.filter((h) => Date.parse(h.timestamp) + 3600000 > now);
  return hours.length && Date.parse(hours[0].timestamp) <= now ? hours : [];
}

/** Compact native grid → approximately 1 km display cells, unchanged score.
 * Entries are north-west corners, matching HeatmapView's polygon convention.
 * Only the selected hour is materialized; no full horizon pixel duplication.
 */
export function forecastPixels(data: LiveForecast, hour: ForecastHour): [number, number, number][] {
  const grid = data.grid;
  return Array.from({ length: grid.rows * grid.cols }, (_, index) => [
    grid.north - Math.floor(index / grid.cols) * grid.lat_step,
    grid.west + (index % grid.cols) * grid.lon_step,
    hour.score / 100,
  ]);
}

export function samePoint(a: Coordinates | null, b: Coordinates): boolean {
  return a?.latitude === b.latitude && a.longitude === b.longitude;
}
