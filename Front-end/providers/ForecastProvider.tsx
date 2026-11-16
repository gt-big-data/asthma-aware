import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { AppState } from "react-native";
import { api } from "../lib/api";
import type { LiveForecast } from "../lib/forecast";
import { samePoint } from "../lib/forecast";
import type { Coordinates } from "../lib/logging";
import { useAuth } from "./AuthProvider";

interface ForecastState {
  point: Coordinates | null; data: LiveForecast | null; loading: boolean; error: string | null;
  now: number; selectPoint: (point: Coordinates) => void; refresh: () => void;
}
const Context = createContext<ForecastState | null>(null);

function ForecastSession({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const [point, setPoint] = useState<Coordinates | null>(null);
  const [data, setData] = useState<LiveForecast | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [now, setNow] = useState(Date.now);
  const selectPoint = useCallback((next: Coordinates) => {
    setPoint((previous) => samePoint(previous, next) ? previous : next);
  }, []);
  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    // A shared request per chosen point. Switching tabs does not refetch.
    if (!session || !point) return;
    const controller = new AbortController();
    setLoading(true); setError(null); setData(null);
    api.forecast(point, controller.signal).then((result) => {
      if (!controller.signal.aborted) { setData(result); setNow(Date.now()); }
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Forecast unavailable.");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
    // Token refresh is handled by api.getAccessToken without repeating this fetch.
  }, [point, session?.user.id, version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // Expire the displayed data on hour boundaries/foreground, without polling
    // the API. The user explicitly refreshes when a newer batch is available.
    if (!data) return;
    const timer = setInterval(() => setNow(Date.now()), 60000);
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") setNow(Date.now());
    });
    return () => { clearInterval(timer); subscription.remove(); };
  }, [data]);
  // A point change renders before its effect clears the previous response.
  // Never briefly label another location's score with the newly selected point.
  const currentData = point && data?.location.lat === point.latitude && data.location.lon === point.longitude ? data : null;
  return <Context.Provider value={{ point, data: currentData, loading, error, now, selectPoint, refresh }}>{children}</Context.Provider>;
}

export function ForecastProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  // Remount on account change, clearing coordinates, requests and cached data.
  return <ForecastSession key={session?.user.id ?? "signed-out"}>{children}</ForecastSession>;
}

export function useForecast() {
  const value = useContext(Context);
  if (!value) throw new Error("ForecastProvider is missing");
  return value;
}
