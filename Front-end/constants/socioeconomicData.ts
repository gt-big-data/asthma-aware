/**
 * Socioeconomic Data Integration (React Native)
 * Manages zip-code-level health equity and socioeconomic indicators
 * for the AsthmaAware app's Equity View.
 *
 * Backend route: GET /map/socioeconomic
 * Falls back to SAMPLE_SOCIO_DATA until the backend is reachable.
 */

const BACKEND_BASE_URL = "http://10.0.2.2:8000";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface ZipcodeDataPoint {
  zipcode: string; // 5-digit ZIP code, e.g. "30303"
  value: number;
}

export type SocioVar =
  | "Poverty Rate"
  | "Unemployment"
  | "Median Income"
  | "Rent Burden"
  | "No Vehicle"
  | "College Degree";

export const SOCIO_VARIABLES: SocioVar[] = [
  "Poverty Rate",
  "Unemployment",
  "Median Income",
  "Rent Burden",
  "No Vehicle",
  "College Degree",
];

export interface VariableMeta {
  unit: string;
  description: string;
  /** Color ramp key used by ChoroplethView */
  colorScheme: "red" | "orange" | "purple" | "green" | "blue" | "teal";
  /** If true, HIGH values are GOOD (e.g. income) — reverses the color ramp */
  higherIsBetter: boolean;
  /** Key in the backend response object */
  apiKey: keyof BackendZipcode;
}

/** Shape of each zip object from GET /map/socioeconomic */
export interface BackendZipcode {
  zipcode: string;
  area_sq_miles: number;
  population: number;
  population_density: number;
  median_age: number;
  children_under_18_rate: number;
  seniors_65_plus_rate: number;
  median_housing_age: number;
  median_household_income: number;
  median_gross_rent: number;
  median_home_value: number;
  poverty_rate: number;
  bachelor_degree_or_higher_rate: number;
  no_vehicle_households_rate: number;
  severe_rent_burden_rate: number;
  overcrowded_housing_rate: number;
  unemployment_rate: number;
}

export const VARIABLE_META: Record<SocioVar, VariableMeta> = {
  "Poverty Rate": {
    unit: "%",
    description: "% of residents with income below the federal poverty line",
    colorScheme: "red",
    higherIsBetter: false,
    apiKey: "poverty_rate",
  },
  "Unemployment": {
    unit: "%",
    description: "% of labor force currently unemployed and seeking work",
    colorScheme: "orange",
    higherIsBetter: false,
    apiKey: "unemployment_rate",
  },
  "Median Income": {
    unit: "$",
    description: "Median household income (ACS 5-year estimate)",
    colorScheme: "green",
    higherIsBetter: true,
    apiKey: "median_household_income",
  },
  "Rent Burden": {
    unit: "%",
    description: "% of renter households spending >50% of income on rent",
    colorScheme: "purple",
    higherIsBetter: false,
    apiKey: "severe_rent_burden_rate",
  },
  "No Vehicle": {
    unit: "%",
    description: "% of households with no access to a personal vehicle",
    colorScheme: "blue",
    higherIsBetter: false,
    apiKey: "no_vehicle_households_rate",
  },
  "College Degree": {
    unit: "%",
    description: "% of adults (25+) with a bachelor's degree or higher",
    colorScheme: "teal",
    higherIsBetter: true,
    apiKey: "bachelor_degree_or_higher_rate",
  },
};

export type SocioDataset = Record<SocioVar, ZipcodeDataPoint[]>;

// ── Sample Data ────────────────────────────────────────────────────────────────
// Atlanta-area ZIP codes (City of Atlanta + inner-ring suburbs).
// Replace with live backend data once /map/socioeconomic is reachable.

export const SAMPLE_SOCIO_DATA: SocioDataset = {
  "Poverty Rate": [
    { zipcode: "30303", value: 34.1 },
    { zipcode: "30305", value:  6.2 },
    { zipcode: "30306", value:  9.4 },
    { zipcode: "30307", value: 11.2 },
    { zipcode: "30308", value: 18.3 },
    { zipcode: "30309", value: 12.5 },
    { zipcode: "30310", value: 31.7 },
    { zipcode: "30311", value: 28.4 },
    { zipcode: "30312", value: 14.6 },
    { zipcode: "30314", value: 42.8 },
    { zipcode: "30315", value: 36.2 },
    { zipcode: "30316", value: 17.9 },
    { zipcode: "30317", value: 15.1 },
    { zipcode: "30318", value: 22.3 },
    { zipcode: "30319", value:  7.8 },
    { zipcode: "30324", value:  8.9 },
    { zipcode: "30326", value:  5.3 },
    { zipcode: "30327", value:  4.1 },
    { zipcode: "30331", value: 25.6 },
    { zipcode: "30337", value: 29.3 },
    { zipcode: "30338", value:  5.7 },
    { zipcode: "30339", value:  7.2 },
    { zipcode: "30341", value: 14.8 },
    { zipcode: "30342", value:  6.9 },
    { zipcode: "30344", value: 24.1 },
    { zipcode: "30349", value: 19.7 },
  ],
  "Unemployment": [
    { zipcode: "30303", value: 18.4 },
    { zipcode: "30305", value:  3.1 },
    { zipcode: "30306", value:  4.2 },
    { zipcode: "30307", value:  5.1 },
    { zipcode: "30308", value:  7.9 },
    { zipcode: "30309", value:  5.6 },
    { zipcode: "30310", value: 16.2 },
    { zipcode: "30311", value: 14.7 },
    { zipcode: "30312", value:  6.8 },
    { zipcode: "30314", value: 22.1 },
    { zipcode: "30315", value: 18.9 },
    { zipcode: "30316", value:  8.4 },
    { zipcode: "30317", value:  7.2 },
    { zipcode: "30318", value: 10.5 },
    { zipcode: "30319", value:  3.9 },
    { zipcode: "30324", value:  4.3 },
    { zipcode: "30326", value:  2.8 },
    { zipcode: "30327", value:  2.3 },
    { zipcode: "30331", value: 12.6 },
    { zipcode: "30337", value: 15.4 },
    { zipcode: "30338", value:  3.2 },
    { zipcode: "30339", value:  3.8 },
    { zipcode: "30341", value:  7.1 },
    { zipcode: "30342", value:  3.5 },
    { zipcode: "30344", value: 11.8 },
    { zipcode: "30349", value:  9.6 },
  ],
  "Median Income": [
    { zipcode: "30303", value: 24300 },
    { zipcode: "30305", value: 112400 },
    { zipcode: "30306", value: 89600 },
    { zipcode: "30307", value: 83200 },
    { zipcode: "30308", value: 64500 },
    { zipcode: "30309", value: 76800 },
    { zipcode: "30310", value: 31200 },
    { zipcode: "30311", value: 35600 },
    { zipcode: "30312", value: 72100 },
    { zipcode: "30314", value: 19800 },
    { zipcode: "30315", value: 26400 },
    { zipcode: "30316", value: 67300 },
    { zipcode: "30317", value: 74900 },
    { zipcode: "30318", value: 54200 },
    { zipcode: "30319", value: 97500 },
    { zipcode: "30324", value: 91200 },
    { zipcode: "30326", value: 118600 },
    { zipcode: "30327", value: 134700 },
    { zipcode: "30331", value: 43800 },
    { zipcode: "30337", value: 36700 },
    { zipcode: "30338", value: 109200 },
    { zipcode: "30339", value: 96400 },
    { zipcode: "30341", value: 58300 },
    { zipcode: "30342", value: 104500 },
    { zipcode: "30344", value: 41200 },
    { zipcode: "30349", value: 55600 },
  ],
  "Rent Burden": [
    { zipcode: "30303", value: 48.2 },
    { zipcode: "30305", value: 12.4 },
    { zipcode: "30306", value: 18.7 },
    { zipcode: "30307", value: 21.3 },
    { zipcode: "30308", value: 31.6 },
    { zipcode: "30309", value: 26.8 },
    { zipcode: "30310", value: 44.1 },
    { zipcode: "30311", value: 39.7 },
    { zipcode: "30312", value: 22.9 },
    { zipcode: "30314", value: 52.3 },
    { zipcode: "30315", value: 47.6 },
    { zipcode: "30316", value: 25.4 },
    { zipcode: "30317", value: 20.8 },
    { zipcode: "30318", value: 35.2 },
    { zipcode: "30319", value: 14.1 },
    { zipcode: "30324", value: 16.3 },
    { zipcode: "30326", value:  9.8 },
    { zipcode: "30327", value:  8.4 },
    { zipcode: "30331", value: 36.9 },
    { zipcode: "30337", value: 41.2 },
    { zipcode: "30338", value: 11.6 },
    { zipcode: "30339", value: 13.7 },
    { zipcode: "30341", value: 27.4 },
    { zipcode: "30342", value: 13.2 },
    { zipcode: "30344", value: 38.5 },
    { zipcode: "30349", value: 29.1 },
  ],
  "No Vehicle": [
    { zipcode: "30303", value: 41.3 },
    { zipcode: "30305", value:  4.8 },
    { zipcode: "30306", value:  8.2 },
    { zipcode: "30307", value:  9.6 },
    { zipcode: "30308", value: 22.4 },
    { zipcode: "30309", value: 17.1 },
    { zipcode: "30310", value: 34.7 },
    { zipcode: "30311", value: 28.9 },
    { zipcode: "30312", value: 12.3 },
    { zipcode: "30314", value: 51.2 },
    { zipcode: "30315", value: 38.6 },
    { zipcode: "30316", value: 13.8 },
    { zipcode: "30317", value: 11.4 },
    { zipcode: "30318", value: 24.7 },
    { zipcode: "30319", value:  5.3 },
    { zipcode: "30324", value:  6.9 },
    { zipcode: "30326", value:  3.7 },
    { zipcode: "30327", value:  2.9 },
    { zipcode: "30331", value: 22.1 },
    { zipcode: "30337", value: 29.4 },
    { zipcode: "30338", value:  4.2 },
    { zipcode: "30339", value:  5.8 },
    { zipcode: "30341", value: 15.6 },
    { zipcode: "30342", value:  5.1 },
    { zipcode: "30344", value: 26.3 },
    { zipcode: "30349", value: 14.8 },
  ],
  "College Degree": [
    { zipcode: "30303", value: 21.4 },
    { zipcode: "30305", value: 78.3 },
    { zipcode: "30306", value: 71.6 },
    { zipcode: "30307", value: 68.9 },
    { zipcode: "30308", value: 52.3 },
    { zipcode: "30309", value: 61.7 },
    { zipcode: "30310", value: 18.6 },
    { zipcode: "30311", value: 22.4 },
    { zipcode: "30312", value: 58.4 },
    { zipcode: "30314", value: 12.1 },
    { zipcode: "30315", value: 14.8 },
    { zipcode: "30316", value: 54.2 },
    { zipcode: "30317", value: 61.3 },
    { zipcode: "30318", value: 38.7 },
    { zipcode: "30319", value: 74.6 },
    { zipcode: "30324", value: 69.8 },
    { zipcode: "30326", value: 82.1 },
    { zipcode: "30327", value: 87.4 },
    { zipcode: "30331", value: 27.3 },
    { zipcode: "30337", value: 19.6 },
    { zipcode: "30338", value: 79.2 },
    { zipcode: "30339", value: 72.5 },
    { zipcode: "30341", value: 41.8 },
    { zipcode: "30342", value: 76.9 },
    { zipcode: "30344", value: 24.7 },
    { zipcode: "30349", value: 32.1 },
  ],
};

// ── Live data store (mutated by initializeSocioData) ──────────────────────────

export let SOCIO_DATA: SocioDataset = {
  "Poverty Rate":   [...SAMPLE_SOCIO_DATA["Poverty Rate"]],
  "Unemployment":   [...SAMPLE_SOCIO_DATA["Unemployment"]],
  "Median Income":  [...SAMPLE_SOCIO_DATA["Median Income"]],
  "Rent Burden":    [...SAMPLE_SOCIO_DATA["Rent Burden"]],
  "No Vehicle":     [...SAMPLE_SOCIO_DATA["No Vehicle"]],
  "College Degree": [...SAMPLE_SOCIO_DATA["College Degree"]],
};

// ── Backend fetch ─────────────────────────────────────────────────────────────
//
// GET /map/socioeconomic
//
// Expected response shape:
// {
//   city: string,
//   dataset_year: number,
//   source: string,
//   zipcodes: BackendZipcode[]
// }
//
// On failure, silently falls back to SAMPLE_SOCIO_DATA.

export async function fetchSocioeconomicData(): Promise<SocioDataset> {
  try {
    const controller = new AbortController();
    const timeoutId  = setTimeout(() => controller.abort(), 10000);

    const response = await fetch(`${BACKEND_BASE_URL}/map/socioeconomic`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();

    if (!data.zipcodes || !Array.isArray(data.zipcodes)) {
      throw new Error("Invalid response: missing zipcodes array");
    }

    const zips: BackendZipcode[] = data.zipcodes;

    const pick = (key: keyof BackendZipcode): ZipcodeDataPoint[] =>
      zips.map((z) => ({ zipcode: z.zipcode, value: z[key] as number }));

    return {
      "Poverty Rate":   pick("poverty_rate"),
      "Unemployment":   pick("unemployment_rate"),
      "Median Income":  pick("median_household_income"),
      "Rent Burden":    pick("severe_rent_burden_rate"),
      "No Vehicle":     pick("no_vehicle_households_rate"),
      "College Degree": pick("bachelor_degree_or_higher_rate"),
    };
  } catch (err) {
    console.warn(
      "[SocioData] Backend unavailable — using sample data:",
      err instanceof Error ? err.message : err
    );
    return SAMPLE_SOCIO_DATA;
  }
}

export async function initializeSocioData(): Promise<void> {
  console.log("[SocioData] Initializing...");
  const data = await fetchSocioeconomicData();
  Object.assign(SOCIO_DATA, data);
  console.log("[SocioData] Ready");
}