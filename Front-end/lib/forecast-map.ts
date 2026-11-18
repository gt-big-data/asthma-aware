export interface MapForecast {
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

export interface MapRaster { image: string; bounds: MapForecast["bounds"] }

export function inForecastBounds(latitude: number, longitude: number, bounds: MapForecast["bounds"]) {
  const [west, south, east, north] = bounds;
  return longitude >= west && longitude < east && latitude > south && latitude <= north;
}

// Bounded image cache shared across tab visits, keyed by immutable run.
export function createFrameCache(limit = 12) {
  const frames = new Map<string, MapFrameImage>();
  const key = (runId: string, index: number) => `${runId}/${index}`;
  return {
    get: (runId: string, index: number) => frames.get(key(runId, index)),
    put: (frame: MapFrameImage) => {
      const id = key(frame.run_id, frame.index);
      frames.delete(id);
      frames.set(id, frame);
      while (frames.size > limit) frames.delete(frames.keys().next().value!);
    },
  };
}
