/**
 * Heatmap Data Integration (React Native)
 * Fetches environmental data from the AsthmaAware backend
 * and converts it to the frontend's LatLngIntensity format
 */

// Configuration
//ange this to your backend URL (e.g., your server IP for physical device testing) Ch
const BACKEND_BASE_URL = "http://10.0.2.2:8000";

// Types
export type LatLngIntensity = [number, number, number];

export interface HeatmapDataset {
  [variable: string]: {
    [timeStep: string]: LatLngIntensity[];
  };
}

// Variables and time steps
export const VARIABLES = ["SO2", "NDVI", "NO2"] as const;
export type Variable = (typeof VARIABLES)[number];

export const TIME_STEPS = ["Current", "24hr", "48hr", "72hr"] as const;
export type TimeStep = (typeof TIME_STEPS)[number];

// Endpoint mapping
const ENDPOINT_MAP: Record<TimeStep, string> = {
  Current: "/map/current",
  "24hr": "/map/forecast/24h",
  "48hr": "/map/forecast/48h",
  "72hr": "/map/forecast/72h",
};

/**
 * Fetch heatmap data from all backend endpoints
 * @returns Promise<HeatmapDataset> - Structured heatmap data ready for visualization
 */
export async function fetchHeatmapData(): Promise<HeatmapDataset> {
  const heatmapData: HeatmapDataset = {
    SO2: { Current: [], "24hr": [], "48hr": [], "72hr": [] },
    NDVI: { Current: [], "24hr": [], "48hr": [], "72hr": [] },
    NO2: { Current: [], "24hr": [], "48hr": [], "72hr": [] },
  };

  try {
    // Fetch data for each time step
    for (const timeStep of TIME_STEPS) {
      const endpoint = ENDPOINT_MAP[timeStep];
      const url = `${BACKEND_BASE_URL}${endpoint}`;

      const controller = new AbortController();

      const timeoutId = setTimeout(() => controller.abort(), 10000)

      console.log(`[HeatmapData] Fetching ${timeStep} from ${url}`);

      const response = await fetch(url, {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
        signal: controller.signal, // 10 second timeout
      });

      if (!response.ok) {
        throw new Error(
          `Failed to fetch ${timeStep}: HTTP ${response.status}`
        );
      }

      const data = await response.json();

      if (!data.cells || !Array.isArray(data.cells)) {
        throw new Error(
          `Invalid response format for ${timeStep}: missing cells array`
        );
      }

      // Convert cells to LatLngIntensity format for each variable
      for (const variable of VARIABLES) {
        const cellsAsIntensity: LatLngIntensity[] = data.cells.map(
          (cell: any) => {
            const featureLower = variable.toLowerCase();
            const intensity = cell[featureLower] ?? 0;
            return [cell.lat, cell.lon, intensity];
          }
        );

        heatmapData[variable][timeStep] = cellsAsIntensity;
      }

      console.log(
        `[HeatmapData] ✓ Loaded ${timeStep}: ${data.cells.length} cells`
      );
    }

    console.log("[HeatmapData] ✓ All data loaded successfully");
    return heatmapData;
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Unknown error fetching heatmap data";
    console.error(`[HeatmapData] ✗ Error: ${message}`);
    throw new Error(message);
  }
}

/**
 * Initialize heatmap data on app startup
 * Call this once when your app loads
 */
export let HEATMAP_DATA: HeatmapDataset = {
  SO2: { Current: [], "24hr": [], "48hr": [], "72hr": [] },
  NDVI: { Current: [], "24hr": [], "48hr": [], "72hr": [] },
  NO2: { Current: [], "24hr": [], "48hr": [], "72hr": [] },
};

export async function initializeHeatmapData(): Promise<void> {
  console.log("[HeatmapData] Initializing...");
  const data = await fetchHeatmapData();
  // Update the exported object
  Object.assign(HEATMAP_DATA, data);
  console.log("[HeatmapData] Ready to use");
}

/**
 * Optional: Mock fallback data if backend is unavailable
 * Useful for development/testing when backend isn't running
 */
export const MOCK_HEATMAP_DATA: HeatmapDataset = {
  SO2: {
    Current: [
      [33.64, -84.55, 0.129],
      [33.64, -84.547, 0.129],
      [33.641, -84.544, 0.129],
      [33.638, -84.551, 0.127],
      [33.643, -84.541, 0.124],
    ],
    "24hr": [
      [33.64, -84.55, 0.1],
      [33.64, -84.547, 0.105],
      [33.641, -84.544, 0.1],
      [33.638, -84.551, 0.098],
      [33.643, -84.541, 0.095],
    ],
    "48hr": [
      [33.64, -84.55, 0.12],
      [33.64, -84.547, 0.125],
      [33.641, -84.544, 0.12],
      [33.638, -84.551, 0.118],
      [33.643, -84.541, 0.115],
    ],
    "72hr": [
      [33.64, -84.55, 0.08],
      [33.64, -84.547, 0.085],
      [33.641, -84.544, 0.08],
      [33.638, -84.551, 0.078],
      [33.643, -84.541, 0.075],
    ],
  },
  NDVI: {
    Current: [
      [33.64, -84.55, 0.85],
      [33.64, -84.547, 0.93],
      [33.641, -84.544, 0.89],
      [33.638, -84.551, 0.78],
      [33.643, -84.541, 0.82],
    ],
    "24hr": [
      [33.64, -84.55, 0.84],
      [33.64, -84.547, 0.92],
      [33.641, -84.544, 0.88],
      [33.638, -84.551, 0.77],
      [33.643, -84.541, 0.81],
    ],
    "48hr": [
      [33.64, -84.55, 0.83],
      [33.64, -84.547, 0.91],
      [33.641, -84.544, 0.87],
      [33.638, -84.551, 0.76],
      [33.643, -84.541, 0.8],
    ],
    "72hr": [
      [33.64, -84.55, 0.82],
      [33.64, -84.547, 0.9],
      [33.641, -84.544, 0.86],
      [33.638, -84.551, 0.75],
      [33.643, -84.541, 0.79],
    ],
  },
  NO2: {
    Current: [
      [33.64, -84.55, 0.048],
      [33.64, -84.547, 0.048],
      [33.641, -84.544, 0.045],
      [33.638, -84.551, 0.052],
      [33.643, -84.541, 0.043],
    ],
    "24hr": [
      [33.64, -84.55, 0.04],
      [33.64, -84.547, 0.04],
      [33.641, -84.544, 0.037],
      [33.638, -84.551, 0.044],
      [33.643, -84.541, 0.035],
    ],
    "48hr": [
      [33.64, -84.55, 0.06],
      [33.64, -84.547, 0.06],
      [33.641, -84.544, 0.057],
      [33.638, -84.551, 0.064],
      [33.643, -84.541, 0.055],
    ],
    "72hr": [
      [33.64, -84.55, 0.03],
      [33.64, -84.547, 0.03],
      [33.641, -84.544, 0.027],
      [33.638, -84.551, 0.034],
      [33.643, -84.541, 0.025],
    ],
  },
};

/**
 * Use mock data instead of fetching from backend
 * Useful for development when backend is unavailable
 */
export function useMockData(): void {
  console.log("[HeatmapData] Using mock data (backend not available)");
  Object.assign(HEATMAP_DATA, MOCK_HEATMAP_DATA);
}