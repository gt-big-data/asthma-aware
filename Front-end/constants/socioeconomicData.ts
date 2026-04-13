/**
 * Socioeconomic Data Integration (React Native)
 * Manages county-level health equity and socioeconomic indicators
 * for the AsthmaAware app's Equity View.
 *
 * Backend routes are stubbed — falls back to SAMPLE_SOCIO_DATA until implemented.
 * Expected backend route: GET /socioeconomic/counties
 */

const BACKEND_BASE_URL = "http://10.0.2.2:8000";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface CountyDataPoint {
  fips: string;   // 5-digit FIPS code, e.g. "13121"
  name: string;   // Display name, e.g. "Fulton"
  value: number;
}

export type SocioVar =
  | "Poverty Rate"
  | "Uninsured %"
  | "Asthma ED Rate"
  | "Median Income"
  | "PM2.5";

export const SOCIO_VARIABLES: SocioVar[] = [
  "Poverty Rate",
  "Uninsured %",
  "Asthma ED Rate",
  "Median Income",
  "PM2.5",
];

export interface VariableMeta {
  unit: string;
  description: string;
  /** Color ramp key used by ChoroplethView */
  colorScheme: "red" | "orange" | "purple" | "green" | "blue";
  /** If true, HIGH values are GOOD (e.g. income) — reverses the color ramp */
  higherIsBetter: boolean;
}

export const VARIABLE_META: Record<SocioVar, VariableMeta> = {
  "Poverty Rate": {
    unit: "%",
    description: "% of population below the federal poverty line",
    colorScheme: "red",
    higherIsBetter: false,
  },
  "Uninsured %": {
    unit: "%",
    description: "% of residents without any health insurance",
    colorScheme: "orange",
    higherIsBetter: false,
  },
  "Asthma ED Rate": {
    unit: "/10k",
    description: "Asthma-related ER visits per 10,000 residents",
    colorScheme: "purple",
    higherIsBetter: false,
  },
  "Median Income": {
    unit: "$",
    description: "Median household income (ACS 5-year estimate)",
    colorScheme: "green",
    higherIsBetter: true,
  },
  "PM2.5": {
    unit: "μg/m³",
    description: "Annual average fine particulate matter concentration",
    colorScheme: "blue",
    higherIsBetter: false,
  },
};

export type SocioDataset = Record<SocioVar, CountyDataPoint[]>;

// ── Sample Data ────────────────────────────────────────────────────────────────
// Atlanta-metro Georgia counties (15-county MSA + adjacent)
// Replace with live backend data once /socioeconomic/* routes are implemented.

export const SAMPLE_SOCIO_DATA: SocioDataset = {
  "Poverty Rate": [
    { fips: "13121", name: "Fulton",    value: 14.2 },
    { fips: "13089", name: "DeKalb",   value: 16.8 },
    { fips: "13135", name: "Gwinnett", value: 10.4 },
    { fips: "13067", name: "Cobb",     value:  8.9 },
    { fips: "13063", name: "Clayton",  value: 22.1 },
    { fips: "13057", name: "Cherokee", value:  7.2 },
    { fips: "13117", name: "Forsyth",  value:  5.8 },
    { fips: "13151", name: "Henry",    value:  9.6 },
    { fips: "13247", name: "Rockdale", value: 13.5 },
    { fips: "13097", name: "Douglas",  value: 12.3 },
    { fips: "13113", name: "Fayette",  value:  6.1 },
    { fips: "13217", name: "Newton",   value: 17.4 },
    { fips: "13255", name: "Spalding", value: 20.8 },
    { fips: "13223", name: "Paulding", value:  8.4 },
    { fips: "13013", name: "Barrow",   value: 11.9 },
  ],
  "Uninsured %": [
    { fips: "13121", name: "Fulton",    value: 11.3 },
    { fips: "13089", name: "DeKalb",   value: 14.7 },
    { fips: "13135", name: "Gwinnett", value: 18.2 },
    { fips: "13067", name: "Cobb",     value: 10.1 },
    { fips: "13063", name: "Clayton",  value: 21.5 },
    { fips: "13057", name: "Cherokee", value:  9.8 },
    { fips: "13117", name: "Forsyth",  value:  8.4 },
    { fips: "13151", name: "Henry",    value: 12.6 },
    { fips: "13247", name: "Rockdale", value: 15.3 },
    { fips: "13097", name: "Douglas",  value: 13.9 },
    { fips: "13113", name: "Fayette",  value:  7.2 },
    { fips: "13217", name: "Newton",   value: 16.8 },
    { fips: "13255", name: "Spalding", value: 19.4 },
    { fips: "13223", name: "Paulding", value: 10.5 },
    { fips: "13013", name: "Barrow",   value: 14.1 },
  ],
  "Asthma ED Rate": [
    { fips: "13121", name: "Fulton",    value:  8.7 },
    { fips: "13089", name: "DeKalb",   value:  9.4 },
    { fips: "13135", name: "Gwinnett", value:  6.2 },
    { fips: "13067", name: "Cobb",     value:  5.8 },
    { fips: "13063", name: "Clayton",  value: 12.1 },
    { fips: "13057", name: "Cherokee", value:  4.3 },
    { fips: "13117", name: "Forsyth",  value:  3.9 },
    { fips: "13151", name: "Henry",    value:  6.5 },
    { fips: "13247", name: "Rockdale", value:  7.8 },
    { fips: "13097", name: "Douglas",  value:  7.2 },
    { fips: "13113", name: "Fayette",  value:  4.1 },
    { fips: "13217", name: "Newton",   value:  8.9 },
    { fips: "13255", name: "Spalding", value: 11.3 },
    { fips: "13223", name: "Paulding", value:  5.5 },
    { fips: "13013", name: "Barrow",   value:  6.8 },
  ],
  "Median Income": [
    { fips: "13121", name: "Fulton",    value:  72800 },
    { fips: "13089", name: "DeKalb",   value:  61400 },
    { fips: "13135", name: "Gwinnett", value:  68500 },
    { fips: "13067", name: "Cobb",     value:  79200 },
    { fips: "13063", name: "Clayton",  value:  48300 },
    { fips: "13057", name: "Cherokee", value:  84600 },
    { fips: "13117", name: "Forsyth",  value: 103500 },
    { fips: "13151", name: "Henry",    value:  74100 },
    { fips: "13247", name: "Rockdale", value:  57800 },
    { fips: "13097", name: "Douglas",  value:  63200 },
    { fips: "13113", name: "Fayette",  value:  95400 },
    { fips: "13217", name: "Newton",   value:  56700 },
    { fips: "13255", name: "Spalding", value:  44900 },
    { fips: "13223", name: "Paulding", value:  78300 },
    { fips: "13013", name: "Barrow",   value:  59100 },
  ],
  "PM2.5": [
    { fips: "13121", name: "Fulton",    value:  9.8 },
    { fips: "13089", name: "DeKalb",   value:  9.6 },
    { fips: "13135", name: "Gwinnett", value:  9.2 },
    { fips: "13067", name: "Cobb",     value:  9.4 },
    { fips: "13063", name: "Clayton",  value: 10.1 },
    { fips: "13057", name: "Cherokee", value:  8.6 },
    { fips: "13117", name: "Forsyth",  value:  8.3 },
    { fips: "13151", name: "Henry",    value:  9.0 },
    { fips: "13247", name: "Rockdale", value:  9.3 },
    { fips: "13097", name: "Douglas",  value:  9.5 },
    { fips: "13113", name: "Fayette",  value:  8.8 },
    { fips: "13217", name: "Newton",   value:  9.1 },
    { fips: "13255", name: "Spalding", value:  9.7 },
    { fips: "13223", name: "Paulding", value:  9.0 },
    { fips: "13013", name: "Barrow",   value:  8.9 },
  ],
};

// ── Live data store (mutated by initializeSocioData) ──────────────────────────

export let SOCIO_DATA: SocioDataset = {
  "Poverty Rate":   [...SAMPLE_SOCIO_DATA["Poverty Rate"]],
  "Uninsured %":    [...SAMPLE_SOCIO_DATA["Uninsured %"]],
  "Asthma ED Rate": [...SAMPLE_SOCIO_DATA["Asthma ED Rate"]],
  "Median Income":  [...SAMPLE_SOCIO_DATA["Median Income"]],
  "PM2.5":          [...SAMPLE_SOCIO_DATA["PM2.5"]],
};

// ── Backend fetch ─────────────────────────────────────────────────────────────
//
// TODO: Implement GET /socioeconomic/counties on the backend.
//
// Expected response shape:
// {
//   counties: Array<{
//     fips: string,          // "13121"
//     name: string,          // "Fulton"
//     poverty_rate: number,  // 14.2
//     uninsured_pct: number, // 11.3
//     asthma_ed_rate: number,// 8.7
//     median_income: number, // 72800
//     pm25: number,          // 9.8
//   }>
// }
//
// On failure, silently falls back to SAMPLE_SOCIO_DATA.

export async function fetchSocioeconomicData(): Promise<SocioDataset> {
  try {
    const controller = new AbortController();
    const timeoutId  = setTimeout(() => controller.abort(), 10000);

    const response = await fetch(`${BACKEND_BASE_URL}/socioeconomic/counties`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();

    if (!data.counties || !Array.isArray(data.counties)) {
      throw new Error("Invalid response: missing counties array");
    }

    return {
      "Poverty Rate":   data.counties.map((c: any) => ({ fips: c.fips, name: c.name, value: c.poverty_rate   })),
      "Uninsured %":    data.counties.map((c: any) => ({ fips: c.fips, name: c.name, value: c.uninsured_pct  })),
      "Asthma ED Rate": data.counties.map((c: any) => ({ fips: c.fips, name: c.name, value: c.asthma_ed_rate })),
      "Median Income":  data.counties.map((c: any) => ({ fips: c.fips, name: c.name, value: c.median_income  })),
      "PM2.5":          data.counties.map((c: any) => ({ fips: c.fips, name: c.name, value: c.pm25           })),
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
