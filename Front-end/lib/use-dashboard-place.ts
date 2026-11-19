import AsyncStorage from "@react-native-async-storage/async-storage";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { parseSelection, selectionKey, type DashboardPlace } from "./saved-locations";

export function useDashboardPlace(userId?: string) {
  const owner = userId ?? "guest";
  const currentOwner = useRef(owner);
  useEffect(() => { currentOwner.current = owner; }, [owner]);
  const [state, setState] = useState<{ owner: string; place: DashboardPlace | null; ready: boolean; error: string }>({
    owner, place: null, ready: false, error: "",
  });
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function restore() {
      try {
        const saved = parseSelection(await AsyncStorage.getItem(selectionKey(userId)));
        const city = saved ? await api.city(saved.id, controller.signal) : null;
        if (active) setState({ owner, place: city && saved ? { ...city, label: saved.label } : null, ready: true, error: "" });
      } catch {
        if (active) setState({ owner, place: null, ready: true, error: "Could not restore your selected place. Choose a location to continue." });
      }
    }
    void restore();
    return () => { active = false; controller.abort(); };
  }, [owner, userId]);

  async function select(place: DashboardPlace | null) {
    try {
      if (place) await AsyncStorage.setItem(selectionKey(userId), JSON.stringify({ id: place.id, label: place.label }));
      else await AsyncStorage.removeItem(selectionKey(userId));
    } catch {
      // The chosen location can still be viewed when device storage is unavailable.
      if (currentOwner.current === owner) setState({ owner, place, ready: true, error: "This selection could not be remembered on this device." });
      return;
    }
    if (currentOwner.current === owner) setState({ owner, place, ready: true, error: "" });
  }
  return { ...(state.owner === owner ? state : { place: null, ready: false, error: "" }), select };
}
