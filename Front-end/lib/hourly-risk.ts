export interface RiskDriver {
  pollutant: "O3" | "NO2" | "SO2" | "PM25";
  exposure: number;
  reference: number;
  unit: string;
  impact: number;
  score_without: number;
}

export interface RiskHour {
  valid_at: string;
  score: number | null;
  general_score: number | null;
  drivers?: RiskDriver[];
}

export function riskColor(score: number) {
  return score < 40 ? "#39845c" : score < 70 ? "#9e6b1f" : "#c54f49";
}

export function riskLabel(score: number) {
  return score < 40 ? "Low" : score < 70 ? "Moderate" : "High";
}

export function localDay(timestamp: string, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date(timestamp));
  const part = (type: string) => parts.find(item => item.type === type)!.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export function windowHours(hours: RiskHour[], window: string, timezone: string) {
  return window === "next" ? hours.slice(0, 24) : hours.filter(hour => localDay(hour.valid_at, timezone) === window);
}

// NASA timestamps represent the midpoint of each hour. Show its start on the axis.
export function hourLabel(timestamp: string, timezone: string, detailed = false) {
  return new Date(new Date(timestamp).getTime() - 30 * 60 * 1000).toLocaleString("en-US", {
    timeZone: timezone, hour: "numeric", ...(detailed ? { weekday: "short", month: "short", day: "numeric", timeZoneName: "short" } : {}),
  });
}

export function scoreRange(hours: RiskHour[]) {
  const scores = hours.flatMap(hour => hour.score === null ? [] : [hour.score]);
  return scores.length ? { min: Math.min(...scores), max: Math.max(...scores) } : null;
}
