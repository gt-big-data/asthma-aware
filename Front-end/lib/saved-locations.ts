export interface GeorgiaCity {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  state: "GA";
  forecast_supported: boolean;
}
export interface SavedLocation {
  city_id: string;
  label: string;
  city_name: string;
  latitude: number;
  longitude: number;
  state: "GA";
  forecast_supported: boolean;
}
export type DashboardPlace = GeorgiaCity & { label: string; kind?: "coordinates" };
export function fromSavedPlace(place: SavedLocation): DashboardPlace {
  return { id: place.city_id, name: place.city_name, label: place.label, latitude: place.latitude,
    longitude: place.longitude, state: "GA", forecast_supported: place.forecast_supported };
}
export function selectionKey(userId?: string) { return `asthmaaware:dashboard-place:v1:${userId ?? "guest"}`; }
export function coordinatePlace(latitude: number, longitude: number, forecast_supported: boolean, label?: string): DashboardPlace {
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180)
    throw new Error("Enter valid latitude and longitude.");
  const lat = Number(latitude.toFixed(5)), lon = Number(longitude.toFixed(5));
  const name = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  return { id: `coordinates:${lat.toFixed(5)}:${lon.toFixed(5)}`, kind: "coordinates", name,
    label: label?.trim().slice(0,80) || name, latitude: lat, longitude: lon, state: "GA", forecast_supported };
}
export function placeLabel(place: DashboardPlace): string {
  if (place.kind === "coordinates") {
    const coordinates = `${place.latitude.toFixed(5)}, ${place.longitude.toFixed(5)}`;
    return place.label === place.name ? `${coordinates} · GA` : `${place.label} · ${coordinates}`;
  }
  return place.label === place.name ? `${place.name}, GA` : `${place.label} · ${place.name}`;
}
export type StoredSelection = { id: string; label: string } | { kind: "coordinates"; latitude: number; longitude: number; label: string };
export function parseSelection(text: string | null): StoredSelection | null {
  if (!text) return null;
  try {
    const value = JSON.parse(text);
    if (value?.kind === "coordinates" && typeof value.latitude === "number" && typeof value.longitude === "number"
        && Number.isFinite(value.latitude) && Number.isFinite(value.longitude) && Math.abs(value.latitude) <= 90 && Math.abs(value.longitude) <= 180
        && typeof value.label === "string" && value.label.trim() && value.label.length <= 80)
      return { kind: "coordinates", latitude: value.latitude, longitude: value.longitude, label: value.label };
    if (value && typeof value.id === "string" && /^13\d{5}$/.test(value.id)
      && typeof value.label === "string" && value.label.trim().length > 0 && value.label.length <= 80)
      return { id: value.id, label: value.label };
  } catch { /* Discard malformed device preferences. */ }
  return null;
}
