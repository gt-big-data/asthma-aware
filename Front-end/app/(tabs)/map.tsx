import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFocusEffect } from "expo-router";
import { StyleSheet, View } from "react-native";
import HeatmapView, { ATLANTA_BOUNDS, type MapBounds } from "../../components/HeatmapView";
import { getCachedUserLocation, getUserLocation, type Coords } from "../../constants/userLocation";
import { api } from "../../lib/api";
import { ApiError } from "../../lib/api-client";
import { createFrameCache, createFrameLoader, nearbyFrameIndices, inForecastBounds, type MapForecast, type MapFrameImage } from "../../lib/forecast-map";

const frameCache = createFrameCache();

export default function MapScreen() {
  const [forecast, setForecast] = useState<MapForecast | null>(null);
  const [hoursAhead, setHoursAhead] = useState(0);
  const [loaded, setLoaded] = useState<MapFrameImage | null>(null);
  const [displayed, setDisplayed] = useState<MapFrameImage | null>(null);
  const [frameFailure, setFrameFailure] = useState<{ id: string; message: string } | null>(null);
  const [status, setStatus] = useState("Loading NASA risk forecast…");
  const [refresh, setRefresh] = useState(0);
  const [focused, setFocused] = useState(false);
  const [location, setLocation] = useState<Coords | null>(() => getCachedUserLocation());
  const [startBounds, setStartBounds] = useState<MapBounds>(ATLANTA_BOUNDS);
  const selected = forecast?.frames[hoursAhead];
  const frameId = `${forecast?.run_id}/${selected?.index}`;
  const raster = useMemo(() => loaded && forecast && selected
    && selected.available && loaded.run_id === forecast.run_id
    ? { image: loaded.image, bounds: forecast.bounds, key: `${loaded.run_id}/${loaded.index}` } : null, [loaded, forecast, selected]);
  const initialized = useRef(false);
  const direction = useRef(1);
  const forecastRef = useRef(forecast);
  const positionRef = useRef(hoursAhead);
  useEffect(() => { forecastRef.current = forecast; positionRef.current = hoursAhead; }, [forecast, hoursAhead]);
  const loader = useMemo(() => createFrameLoader((run, index, signal) => api.mapFrame(run, index, signal), frameCache), []);
  const changeHour = useCallback((position: number) => {
    direction.current = position < positionRef.current ? -1 : 1;
    positionRef.current = position;
    const current = forecastRef.current;
    const target = current?.frames[position];
    if (current && target?.available) {
      const cached = frameCache.get(current.run_id, target.index);
      if (cached) setLoaded(cached);
    }
    setHoursAhead(position);
  }, []);
  const rasterShown = useCallback((key: string) => {
    if (loaded && key === `${loaded.run_id}/${loaded.index}`) setDisplayed(loaded);
  }, [loaded]);
  useEffect(() => { if (!focused) loader.stop(); return () => loader.stop(); }, [focused, loader]);

  useFocusEffect(useCallback(() => {
    setFocused(true);
    return () => setFocused(false);
  }, []));

  useEffect(() => {
    if (!focused) return;
    const controller = new AbortController();
    let active = true;
    async function load() {
      setStatus("Loading NASA risk forecast…");
      try {
        const result = await api.mapForecast(controller.signal);
        if (!active) return;
        const previous = forecastRef.current;
        const selectedTime = previous?.frames[positionRef.current]?.valid_at;
        const position = Math.max(0, result.frames.findIndex(frame => frame.valid_at === selectedTime));
        if (previous?.run_id !== result.run_id) { setLoaded(null); setDisplayed(null); loader.stop(); }
        setForecast(result);
        setHoursAhead(position);
        positionRef.current = position;
        setStatus("");
        const cached = getCachedUserLocation();
        if (!initialized.current && cached && inForecastBounds(cached.latitude, cached.longitude, result.bounds)) {
          setStartBounds([[cached.longitude - 0.425, cached.latitude - 0.3], [cached.longitude + 0.425, cached.latitude + 0.3]]);
        }
        initialized.current = true;
      } catch {
        if (active) setStatus("Could not refresh the risk forecast. Tap to retry.");
      }
    }
    void load();
    const timer = setInterval(() => setRefresh(value => value + 1), 5 * 60 * 1000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [focused, refresh, loader]);

  useEffect(() => {
    let active = true;
    void getUserLocation().then(coords => { if (active) setLocation(coords); }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!focused || !forecast || !selected) return;
    let active = true;
    if (!selected.available) {
      loader.stop();
      return;
    }
    const cached = frameCache.get(forecast.run_id, selected.index);
    const timer = setTimeout(async () => {
      try {
        const frame = await loader.select(forecast.run_id, selected.index,
          nearbyFrameIndices(forecast.frames, hoursAhead, direction.current));
        if (!active) return;
        setLoaded(frame);
      } catch (error) {
        if (!active) return;
        if (error instanceof ApiError && (error.status === 409 || error.status === 404)) {
          setRefresh(value => value + 1);
        } else setFrameFailure({ id: frameId, message: "Risk layer unavailable. Tap to retry." });
      }
    }, cached ? 0 : 50);
    return () => { active = false; clearTimeout(timer); };
  }, [forecast, selected, focused, refresh, frameId, hoursAhead, loader]);

  const pending = selected?.available && (!displayed || displayed.run_id !== forecast?.run_id || displayed.index !== selected.index);
  const visibleStatus = !forecast ? status : !selected?.available ? "No risk data for this hour."
    : frameFailure?.id === frameId && pending ? frameFailure.message
    : status;

  const formatTime = (time: string) => new Date(time).toLocaleString("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
  const validLabel = selected ? pending && displayed?.run_id === forecast?.run_id
    ? `Showing ${formatTime(displayed.valid_at)} · Selected ${formatTime(selected.valid_at)}`
    : formatTime(selected.valid_at) : "";
  const note = forecast ? ["General population risk", forecast.provisional ? "Development estimate" : "",
    forecast.stale ? "NASA run older than 24h" : "", "Coverage limited to shaded area"].filter(Boolean).join(" · ") : "";

  return <View style={styles.root}>
    <HeatmapView key={JSON.stringify(startBounds)} raster={raster} hoursAhead={hoursAhead} maxHours={Math.max(0, (forecast?.frames.length ?? 1) - 1)}
      onHoursAheadChange={changeHour} onRasterShown={rasterShown} userLocation={location} visualization="heat" startBounds={startBounds}
      gridStepDegrees={forecast?.grid_step_degrees}
      status={visibleStatus} note={note} validLabel={validLabel} colors={forecast?.colors}
      onRetry={() => setRefresh(value => value + 1)} />
  </View>;
}

const styles = StyleSheet.create({ root: { flex: 1, backgroundColor: "#f4f7f4" } });
