export type Coordinates = { latitude: number; longitude: number };
export type LocationSource = "current" | "selected";

export function localDate(now: Date, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const value = (type: string) => parts.find((part) => part.type === type)!.value;
  return `${value("year")}-${value("month")}-${value("day")}`;
}

export function availableDates(timezone: string, now = new Date()): string[] {
  const [year, month, day] = localDate(now, timezone).split("-").map(Number);
  return Array.from({ length: 6 }, (_, offset) =>
    new Date(Date.UTC(year, month - 1, day - offset)).toISOString().slice(0, 10));
}

export function dateLabel(day: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" })
    .format(new Date(`${day}T12:00:00Z`));
}

export function validCoordinates(value: Coordinates): boolean {
  return Number.isFinite(value.latitude) && Number.isFinite(value.longitude)
    && Math.abs(value.latitude) <= 90 && Math.abs(value.longitude) <= 180;
}

export function parseCoordinates(latitude: string, longitude: string): Coordinates | null {
  if (!latitude.trim() || !longitude.trim()) return null;
  const point = { latitude: Number(latitude), longitude: Number(longitude) };
  return validCoordinates(point) ? point : null;
}
