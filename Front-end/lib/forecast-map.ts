export type MapLayerId = "risk" | "personal" | "o3" | "no2" | "so2" | "pm25" | "temperature" | "pm10" | "co";
export interface MapLayer {
  id: MapLayerId; label: string; unit: string; minimum: number; maximum: number;
  colors: string[]; description: string; processing: string; experimental: boolean;
}
export const GENERAL_RISK_LAYER: MapLayer = {
  id: "risk", label: "General risk", unit: "score / 100", minimum: 0, maximum: 100,
  colors: ["#2547a8", "#20b9c7", "#69cf71", "#f4d84a", "#e53935"],
  description: "General population risk · not personalized", processing: "Rolling exposure model", experimental: false,
};

export const PERSONAL_RISK_LAYER: MapLayer = {
  ...GENERAL_RISK_LAYER, id: "personal", label: "Personalized asthma risk",
  description: "Your asthma risk · learned from daily symptom reports",
  processing: "Same personalized scoring model as your dashboard",
};

export interface MapForecast {
  personalization?: { matched_logs: number; pending_logs: number; revision: number; still_learning: boolean } | null;
  layer?: MapLayer;
  layers?: MapLayer[];
  run_id: string;
  source_run: string;
  issued_at: string;
  stale: boolean;
  provisional: boolean;
  grid_step_degrees: number;
  bounds: [number, number, number, number];
  colors: string[];
  score_min: number;
  score_max: number;
  frames: { index: number; valid_at: string; available: boolean }[];
}

export interface MapFrameImage {
  run_id: string;
  index: number;
  valid_at: string;
  available: boolean;
  image: string;
}

export interface MapRaster { image: string; bounds: MapForecast["bounds"]; key?: string; transition?: "replace" }

/** Geographic cell edges, anchored to the forecast footprint rather than rounded city coordinates. */
export function createCellGrid(bounds: MapForecast["bounds"], step: number) {
  const [west, south, east, north] = bounds;
  if (![...bounds, step].every(Number.isFinite) || step <= 0 || west >= east || south >= north) {
    throw new Error("Invalid forecast grid");
  }
  const columns = Math.round((east - west) / step);
  const rows = Math.round((north - south) / step);
  if (columns < 1 || rows < 1 || columns + rows > 5000
      || Math.abs(columns * step - (east - west)) > 1e-7
      || Math.abs(rows * step - (north - south)) > 1e-7) {
    throw new Error("Forecast bounds must align with the cell size");
  }
  const features = [];
  const edge = (value: number) => Number(value.toFixed(10));
  for (let column = 0; column <= columns; column++) {
    const longitude = edge(west + column * step);
    features.push({ type: "Feature" as const, properties: {}, geometry: {
      type: "LineString" as const, coordinates: [[longitude, south], [longitude, north]],
    } });
  }
  for (let row = 0; row <= rows; row++) {
    const latitude = edge(north - row * step);
    features.push({ type: "Feature" as const, properties: {}, geometry: {
      type: "LineString" as const, coordinates: [[west, latitude], [east, latitude]],
    } });
  }
  return { type: "FeatureCollection" as const, features };
}

export function inForecastBounds(latitude: number, longitude: number, bounds: MapForecast["bounds"]) {
  const [west, south, east, north] = bounds;
  return longitude >= west && longitude < east && latitude > south && latitude <= north;
}

// Bounded image cache shared across tab visits, keyed by immutable run.
export function createFrameCache(limit = 48, maxBytes = 16 * 1024 * 1024) {
  const frames = new Map<string, MapFrameImage>();
  let bytes = 0;
  const size = (frame: MapFrameImage) => frame.image.length * 2;
  const key = (runId: string, index: number) => `${runId}/${index}`;
  return {
    get: (runId: string, index: number) => {
      const id = key(runId, index);
      const frame = frames.get(id);
      if (frame) { frames.delete(id); frames.set(id, frame); }
      return frame;
    },
    put: (frame: MapFrameImage) => {
      const id = key(frame.run_id, frame.index);
      const previous = frames.get(id);
      if (previous) { frames.delete(id); bytes -= size(previous); }
      if (size(frame) > maxBytes) return;
      frames.set(id, frame);
      bytes += size(frame);
      while (frames.size > limit || bytes > maxBytes) {
        const oldest = frames.keys().next().value!;
        bytes -= size(frames.get(oldest)!);
        frames.delete(oldest);
      }
    },
  };
}

/** Prefer the direction of travel; never fetch known unavailable hours. */
export function nearbyFrameIndices(frames: MapForecast["frames"], position: number, direction = 1) {
  const positions = [position];
  for (let distance = 1; distance <= 8; distance++) {
    positions.push(position + direction * distance);
    if (distance <= 3) positions.push(position - direction * distance);
  }
  return positions.filter(i => i >= 0 && i < frames.length && frames[i].available).map(i => frames[i].index);
}

type FrameCache = ReturnType<typeof createFrameCache>;
type FrameJob = {
  run: string; index: number; controller: AbortController; promise: Promise<MapFrameImage>;
  resolve: (frame: MapFrameImage) => void; reject: (error: unknown) => void;
};

/** Two requests maximum, shared promises, and a replaceable foreground-first queue. */
export function createFrameLoader(fetchFrame: (run: string, index: number, signal: AbortSignal) => Promise<MapFrameImage>,
  cache: FrameCache = createFrameCache(), options: { cancelActive?: boolean } = {}) {
  const jobs = new Map<string, FrameJob>();
  const active = new Set<FrameJob>();
  let queue: FrameJob[] = [];
  const key = (run: string, index: number) => `${run}/${index}`;
  const abortError = () => Object.assign(new Error("Obsolete map frame"), { name: "AbortError" });
  function pump() {
    while (active.size < 2 && queue.length) {
      const job = queue.shift()!;
      if (job.controller.signal.aborted) continue;
      active.add(job);
      void Promise.resolve().then(() => fetchFrame(job.run, job.index, job.controller.signal)).then(frame => {
        if (job.controller.signal.aborted) throw abortError();
        if (frame.run_id !== job.run || frame.index !== job.index || !frame.available || !frame.image) {
          throw new Error("Mismatched or unavailable forecast frame");
        }
        cache.put(frame);
        job.resolve(frame);
      }).catch(job.reject).finally(() => {
        active.delete(job);
        if (jobs.get(key(job.run, job.index)) === job) jobs.delete(key(job.run, job.index));
        pump();
      });
    }
  }
  function cancel(job: FrameJob) {
    job.controller.abort();
    job.reject(abortError());
    jobs.delete(key(job.run, job.index));
  }
  return {
    select(run: string, index: number, neighbors: number[]) {
      const indices = [...new Set([index, ...neighbors])];
      const wanted = new Set(indices.map(i => key(run, i)));
      for (const [id, job] of jobs) {
        // Finishing an active physical-field request avoids filling the API
        // thread pool with work whose HTTP requests were already abandoned.
        if (!wanted.has(id) && (!active.has(job) || options.cancelActive !== false)) cancel(job);
      }
      const target = cache.get(run, index);
      queue = [];
      for (const i of indices) {
        if (cache.get(run, i)) continue;
        const id = key(run, i);
        let job = jobs.get(id);
        if (!job) {
          let resolve!: FrameJob["resolve"], reject!: FrameJob["reject"];
          const promise = new Promise<MapFrameImage>((yes, no) => { resolve = yes; reject = no; });
          // Neighbor failures are opportunistic; the selected caller still observes rejection.
          void promise.catch(() => {});
          job = { run, index: i, controller: new AbortController(), promise, resolve, reject };
          jobs.set(id, job);
        }
        if (!active.has(job)) queue.push(job);
      }
      const result = target ? Promise.resolve(target) : jobs.get(key(run, index))!.promise;
      pump();
      return result;
    },
    stop() {
      queue = [];
      for (const job of jobs.values()) cancel(job);
    },
  };
}


export interface MapCell {
  row: number; column: number; latitude: number; longitude: number;
  bounds: MapForecast["bounds"]; grid_step_degrees: number;
  risk_score: number | null; risk_kind: "general" | "personal";
  layer: MapLayerId; layer_value: number | null; layer_unit: string;
  valid_at: string; source_run: string; in_georgia: boolean; forecast_supported: boolean;
}
export function forecastCellAt(latitude: number, longitude: number, bounds: MapForecast["bounds"]): {latitude: number; longitude: number; bounds: MapForecast["bounds"]} | null {
  if (!inForecastBounds(latitude, longitude, bounds)) return null;
  const [west, , , north] = bounds;
  const row = Math.floor((north - latitude) / .01), col = Math.floor((longitude - west) / .01);
  const rounded = (n: number) => Number(n.toFixed(6));
  return { latitude: rounded(north - (row + .5) * .01), longitude: rounded(west + (col + .5) * .01),
    bounds: [rounded(west + col * .01), rounded(north - (row + 1) * .01), rounded(west + (col + 1) * .01), rounded(north - row * .01)] };
}
