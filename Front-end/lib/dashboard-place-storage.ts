import AsyncStorage from "@react-native-async-storage/async-storage";
import { selectionKey, type DashboardPlace } from "./saved-locations";

const listeners = new Set<(owner: string, place: DashboardPlace | null) => void>();
export function subscribeDashboardPlace(listener: (owner: string, place: DashboardPlace | null) => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export async function storeDashboardPlace(userId: string | undefined, place: DashboardPlace | null) {
  const value = place?.kind === "coordinates"
    ? { kind: "coordinates", latitude: place.latitude, longitude: place.longitude, label: place.label }
    : place ? { id: place.id, label: place.label } : null;
  if (value) await AsyncStorage.setItem(selectionKey(userId), JSON.stringify(value));
  else await AsyncStorage.removeItem(selectionKey(userId));
  for (const listener of listeners) listener(userId ?? "guest", place);
}
