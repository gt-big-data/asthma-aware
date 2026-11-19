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
export type DashboardPlace = GeorgiaCity & { label: string };
export function fromSavedPlace(place: SavedLocation): DashboardPlace {
  return { id: place.city_id, name: place.city_name, label: place.label, latitude: place.latitude,
    longitude: place.longitude, state: "GA", forecast_supported: place.forecast_supported };
}
export function selectionKey(userId?: string) { return `asthmaaware:dashboard-place:v1:${userId ?? "guest"}`; }
export function parseSelection(text: string | null): { id: string; label: string } | null {
  if (!text) return null;
  try {
    const value = JSON.parse(text);
    if (value && typeof value.id === "string" && /^13\d{5}$/.test(value.id)
      && typeof value.label === "string" && value.label.trim().length > 0 && value.label.length <= 80)
      return { id: value.id, label: value.label };
  } catch { /* Discard malformed device preferences. */ }
  return null;
}
