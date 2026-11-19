import AsyncStorage from "@react-native-async-storage/async-storage";
import { useCallback, useEffect, useRef, useState } from "react";
import { useFocusEffect } from "expo-router";
import { api } from "./api";
import { coordinatePlace, parseSelection, selectionKey, type DashboardPlace } from "./saved-locations";

import { storeDashboardPlace, subscribeDashboardPlace } from "./dashboard-place-storage";

export function useDashboardPlace(userId?: string) {
  const owner = userId ?? "guest";
  const currentOwner = useRef(owner);
  useEffect(() => { currentOwner.current = owner; }, [owner]);
  const [state, setState] = useState<{ owner: string; place: DashboardPlace | null; ready: boolean; error: string }>({
    owner, place: null, ready: false, error: "",
  });
  useFocusEffect(useCallback(() => {
    // Map and dashboard can be loaded in separate route bundles. Re-read the
    // persisted selection on return instead of relying only on live listeners.
    setState(current => ({ ...current, owner, ready: false, error: "" }));
    let active = true;
    const controller = new AbortController();
    let changed = false;
    const unsubscribe = subscribeDashboardPlace((selectedOwner, place) => {
      if (selectedOwner === owner && active) { changed = true; setState({ owner, place, ready: true, error: "" }); }
    });
    async function restore() {
      let rememberedCoordinates: DashboardPlace | null = null;
      try {
        const saved = parseSelection(await AsyncStorage.getItem(selectionKey(userId)));
        let place: DashboardPlace | null = null;
        if (saved && "kind" in saved) {
          rememberedCoordinates = coordinatePlace(saved.latitude, saved.longitude, false, saved.label);
          const coverage = await api.locationCoverage(saved.latitude, saved.longitude, controller.signal);
          if (!coverage.in_georgia) throw new Error("Selected coordinate is outside Georgia");
          place = coordinatePlace(saved.latitude, saved.longitude, coverage.forecast_supported, saved.label);
        } else if (saved) place = { ...await api.city(saved.id, controller.signal), label: saved.label };
        if (active && !changed) setState({ owner, place, ready: true, error: "" });
      } catch {
        if (active && !changed) setState({ owner, place: rememberedCoordinates, ready: true,
          error: rememberedCoordinates ? "Could not verify your selected coordinates. Choose the location again to retry." : "Could not restore your selected place. Choose a location to continue." });
      }
    }
    void restore();
    return () => { active = false; controller.abort(); unsubscribe(); };
  }, [owner, userId]));

  async function select(place: DashboardPlace | null) {
    try {
      await storeDashboardPlace(userId, place);
    } catch {
      // The chosen location can still be viewed when device storage is unavailable.
      if (currentOwner.current === owner) setState({ owner, place, ready: true, error: "This selection could not be remembered on this device." });
      return;
    }
    if (currentOwner.current === owner) setState({ owner, place, ready: true, error: "" });
  }
  return { ...(state.owner === owner ? state : { place: null, ready: false, error: "" }), select };
}
