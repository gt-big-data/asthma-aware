import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFocusEffect } from "expo-router";
import { StyleSheet, View } from "react-native";
import HeatmapView, { ATLANTA_BOUNDS, type MapBounds } from "../../components/HeatmapView";
import { getCachedUserLocation, getUserLocation, type Coords } from "../../constants/userLocation";
import { api } from "../../lib/api";
import { ApiError } from "../../lib/api-client";
import { createFrameCache, inForecastBounds, type MapForecast, type MapFrameImage } from "../../lib/forecast-map";

const frameCache = createFrameCache();

export default function MapScreen() {
  const [forecast, setForecast] = useState<MapForecast | null>(null);
  const [hoursAhead, setHoursAhead] = useState(0);
  const [loaded, setLoaded] = useState<MapFrameImage | null>(null);
  const [frameFailure, setFrameFailure] = useState<{ id: string; message: string } | null>(null);
  const [status, setStatus] = useState("Loading NASA risk forecast…");
  const [refresh, setRefresh] = useState(0);
  const [focused, setFocused] = useState(false);
  const [location, setLocation] = useState<Coords | null>(() => getCachedUserLocation());
  const [startBounds, setStartBounds] = useState<MapBounds>(ATLANTA_BOUNDS);
  const selected = forecast?.frames[hoursAhead];
  const frameId = `${forecast?.run_id}/${selected?.index}`;
  const raster = useMemo(() => loaded && forecast && selected
    && loaded.run_id === forecast.run_id && loaded.index === selected.index
    ? { image: loaded.image, bounds: forecast.bounds } : null, [loaded, forecast, selected]);
  const initialized = useRef(false);

  useFocusEffect(useCallback(() => {
    setFocused(true);
    return () => setFocused(false);
  }, []));

  useEffect(() => {
    if (!focused) return;
    const controller = new AbortController();
    let active = true;
    async function load() {
      setForecast(null);
      setStatus("Loading NASA risk forecast…");
      try {
        const result = await api.mapForecast(controller.signal);
        if (!active) return;
        setForecast(result);
        setHoursAhead(0);
        const cached = getCachedUserLocation();
        if (!initialized.current && cached && inForecastBounds(cached.latitude, cached.longitude, result.bounds)) {
          setStartBounds([[cached.longitude - 0.425, cached.latitude - 0.3], [cached.longitude + 0.425, cached.latitude + 0.3]]);
        }
        initialized.current = true;
      } catch {
        if (active) setStatus("Risk forecast unavailable. Tap to retry.");
      }
    }
    void load();
    const timer = setInterval(() => setRefresh(value => value + 1), 5 * 60 * 1000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [focused, refresh]);

  useEffect(() => {
    let active = true;
    void getUserLocation().then(coords => { if (active) setLocation(coords); }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!focused || !forecast || !selected) return;
    const controller = new AbortController();
    let active = true;
    if (!selected.available) {
      return () => controller.abort();
    }
    const timer = setTimeout(async () => {
      try {
        const frame = frameCache.get(forecast.run_id, selected.index)
          ?? await api.mapFrame(forecast.run_id, selected.index, controller.signal);
        if (!active) return;
        if (frame.run_id !== forecast.run_id || frame.index !== selected.index) throw new Error("Mismatched forecast frame");
        frameCache.put(frame);
        setLoaded(frame);
      } catch (error) {
        if (!active) return;
        if (error instanceof ApiError && (error.status === 409 || error.status === 404)) {
          setRefresh(value => value + 1);
        } else setFrameFailure({ id: frameId, message: "Risk layer unavailable. Tap to retry." });
      }
    }, 120);
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [forecast, selected, focused, refresh, frameId]);

  const visibleStatus = !forecast ? status : !selected?.available ? "No risk data for this hour."
    : raster ? "" : frameFailure?.id === frameId ? frameFailure.message : "Loading risk layer…";

  const validLabel = selected ? new Date(selected.valid_at).toLocaleString("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  }) : "";
  const note = forecast ? ["General population risk", forecast.provisional ? "Development estimate" : "",
    forecast.stale ? "NASA run older than 24h" : "", "Coverage limited to shaded area"].filter(Boolean).join(" · ") : "";

  return <View style={styles.root}>
    <HeatmapView key={JSON.stringify(startBounds)} raster={raster} hoursAhead={hoursAhead} maxHours={Math.max(0, (forecast?.frames.length ?? 1) - 1)}
      onHoursAheadChange={setHoursAhead} userLocation={location} visualization="heat" startBounds={startBounds}
      status={visibleStatus} note={note} validLabel={validLabel} colors={forecast?.colors}
      onRetry={() => setRefresh(value => value + 1)} />
  </View>;
}

const styles = StyleSheet.create({ root: { flex: 1, backgroundColor: "#f4f7f4" } });
