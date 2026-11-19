import type { RiskHour } from "./hourly-risk";

const HOUR = 3600000;
export interface LowerRiskPeriod {
  start: string;
  end: string;
  min: number;
  peak: number;
  mean: number;
  hours: number;
}

/** Contiguous real intervals only; missing scores break a period. Lower is relative, not a safety rating. */
export function lowerRiskPeriods(hours: RiskHour[], now = Date.now()) {
  const unique = new Map(hours.map(hour => [hour.valid_at, hour]));
  const upcoming = [...unique.values()].filter(hour => {
    const midpoint = Date.parse(hour.valid_at);
    return Number.isFinite(midpoint) && midpoint + HOUR / 2 > now && midpoint + HOUR / 2 <= now + 48 * HOUR;
  }).sort((a, b) => Date.parse(a.valid_at) - Date.parse(b.valid_at));
  const finite = (score: number | null): score is number => score !== null && Number.isFinite(score) && score >= 0 && score <= 100;
  const scores = upcoming.flatMap(hour => finite(hour.score) ? [hour.score] : []).sort((a, b) => a - b);
  const typical = scores.length ? (scores[Math.floor((scores.length - 1) / 2)] + scores[Math.floor(scores.length / 2)]) / 2 : null;
  const threshold = typical === null ? null : typical - 5;
  const periods: LowerRiskPeriod[] = [];
  let group: RiskHour[] = [];
  function finish() {
    if (group.length) {
      const start = Math.max(now, Date.parse(group[0].valid_at) - HOUR / 2);
      const end = Date.parse(group[group.length - 1].valid_at) + HOUR / 2;
      const values = group.map(hour => hour.score!);
      if (end - start >= 2 * HOUR) periods.push({ start: new Date(start).toISOString(), end: new Date(end).toISOString(),
        min: Math.min(...values), peak: Math.max(...values), mean: values.reduce((a, b) => a + b, 0) / values.length,
        hours: (end - start) / HOUR });
    }
    group = [];
  }
  // Prefer the relative troughs, even when the whole outlook is already low.
  // If no sustained trough exists, still highlight genuinely low-score periods.
  for (const mode of ["relative", "low"]) {
    for (const hour of upcoming) {
      if (group.length && Date.parse(hour.valid_at) - Date.parse(group[group.length - 1].valid_at) !== HOUR) finish();
      if (threshold !== null && finite(hour.score) && (mode === "relative" ? hour.score <= threshold : hour.score < 40)) group.push(hour);
      else finish();
    }
    finish();
    if (periods.length) break;
  }
  // Pick the three strongest periods, then present them in chronological order.
  const best = periods.sort((a, b) => a.mean - b.mean || Date.parse(a.start) - Date.parse(b.start)).slice(0, 3)
    .sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  return { periods: best, typical, availableHours: scores.length };
}

export interface ForecastInput {
  variable: string;
  source_run: string | null;
  issued_at: string | null;
  collection: string | null;
}

export interface ForecastDetails {
  forecast_start: string;
  forecast_end: string;
  source_remaining_hours: number;
  displayed_through: string | null;
  displayed_hours: number;
  scored_hours: number;
  input_sources: ForecastInput[];
}

export function groupForecastInputs(inputs: ForecastInput[]) {
  const groups = new Map<string, { issuedAt: string | null; run: string | null; variables: string[] }>();
  for (const input of inputs) {
    const parsed = input.issued_at ? Date.parse(input.issued_at) : NaN;
    const issuedAt = Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;
    const key = `${issuedAt}/${input.source_run}`;
    const group = groups.get(key) ?? { issuedAt, run: input.source_run, variables: [] };
    group.variables.push(input.variable);
    groups.set(key, group);
  }
  return [...groups.values()];
}
