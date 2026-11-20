import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFocusEffect, useRouter, useLocalSearchParams } from "expo-router";
import { StyleSheet, View } from "react-native";
import MapCellPopup from "../../components/MapCellPopup";
import { coordinatePlace } from "../../lib/saved-locations";
import { storeDashboardPlace } from "../../lib/dashboard-place-storage";
import HeatmapView, { ATLANTA_BOUNDS, type MapBounds } from "../../components/HeatmapView";
import { getCachedUserLocation, getUserLocation, type Coords } from "../../constants/userLocation";
import { api } from "../../lib/api";
import { ApiError } from "../../lib/api-client";
import { GENERAL_RISK_LAYER, PERSONAL_RISK_LAYER, type MapLayerId, type MapCell, forecastCellAt, createFrameCache, createFrameLoader, nearbyFrameIndices, inForecastBounds, type MapForecast, type MapFrameImage } from "../../lib/forecast-map";

import { useAuth } from "../../providers/AuthProvider";

const publicFrameCache = createFrameCache();

export default function MapScreen() {
  const { session } = useAuth();
  const userId = session?.user.id ?? null;
  // Remount all personal state on sign-out/account changes, including the iframe.
  return <UserMapScreen key={userId ?? "guest"} userId={userId} />;
}

function UserMapScreen({ userId }: { userId: string | null }) {
  const router = useRouter();
  const { pickDashboard } = useLocalSearchParams<{pickDashboard?: string}>();
  const picking = pickDashboard === "1";
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const [cellLocation, setCellLocation] = useState<ReturnType<typeof forecastCellAt>>(null);
  const [cellState, setCellState] = useState<{key:string;data:MapCell|null;error:string}>({key:"",data:null,error:""});
  const [cellRefresh, setCellRefresh] = useState(0);
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const personalFrameCache = useMemo(() => createFrameCache(), []);

  const [layer, setLayer] = useState<MapLayerId>(() => userId ? "personal" : "risk");
  const [layers, setLayers] = useState([GENERAL_RISK_LAYER, PERSONAL_RISK_LAYER]);
  const selectedTimeRef = useRef<string | undefined>(undefined);
  const [forecast, setForecast] = useState<MapForecast | null>(null);
  const [hoursAhead, setHoursAhead] = useState(0);
  const [loaded, setLoaded] = useState<MapFrameImage | null>(null);
  const [displayed, setDisplayed] = useState<MapFrameImage | null>(null);
  const [frameFailure, setFrameFailure] = useState<{ id: string; message: string } | null>(null);
  const [status, setStatus] = useState("Loading NASA forecast…");
  const [refresh, setRefresh] = useState(0);
  const [focused, setFocused] = useState(false);
  const [location, setLocation] = useState<Coords | null>(() => getCachedUserLocation());
  const [startBounds, setStartBounds] = useState<MapBounds>(ATLANTA_BOUNDS);
  const frameCache = layer === "personal" ? personalFrameCache : publicFrameCache;
  const selected = forecast?.frames[hoursAhead];
  const frameId = `${forecast?.run_id}/${selected?.index}`;
  const raster = useMemo(() => loaded && forecast && selected
    && selected.available && loaded.run_id === forecast.run_id
    ? { image: loaded.image, bounds: forecast.bounds, key: `${loaded.run_id}/${loaded.index}`, ...(layer !== "risk" ? { transition: "replace" as const } : {}) } : null, [loaded, forecast, selected, layer]);
  const initialized = useRef(false);
  const direction = useRef(1);
  const forecastRef = useRef(forecast);
  const positionRef = useRef(hoursAhead);
  useEffect(() => { forecastRef.current = forecast; positionRef.current = hoursAhead; }, [forecast, hoursAhead]);
  const loader = useMemo(() => createFrameLoader((run, index, signal) => api.mapFrame(run, index, signal, layer), frameCache, { cancelActive: layer === "risk" }), [layer, frameCache]);
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
    setFrameFailure(null);
  }, [frameCache]);
  const changeLayer = useCallback((next: MapLayerId) => {
    if (next === layer) return;
    selectedTimeRef.current = forecastRef.current?.frames[positionRef.current]?.valid_at ?? selectedTimeRef.current;
    loader.stop();
    forecastRef.current = null;
    setForecast(null); setLoaded(null); setDisplayed(null); setFrameFailure(null); setStatus("Loading NASA forecast…");
    setLayer(next);
  }, [layer, loader]);
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
    if (layer === "personal" && !userId) return;
    const controller = new AbortController();
    let active = true;
    async function load() {
      setStatus("Loading NASA forecast…");
      setFrameFailure(null);
      try {
        const result = await api.mapForecast(controller.signal, layer);
        if (!active) return;
        const previous = forecastRef.current;
        const selectedTime = previous?.frames[positionRef.current]?.valid_at ?? selectedTimeRef.current;
        const position = Math.max(0, result.frames.findIndex(frame => frame.valid_at === selectedTime));
        if (previous?.run_id !== result.run_id) { setLoaded(null); setDisplayed(null); loader.stop(); }
        setForecast(result);
        setLayers([GENERAL_RISK_LAYER, PERSONAL_RISK_LAYER, ...(result.layers ?? []).filter(item => item.id !== "personal" && item.id !== "risk")]);
        selectedTimeRef.current = undefined;
        setHoursAhead(position);
        positionRef.current = position;
        setStatus("");
        const cached = getCachedUserLocation();
        if (!initialized.current && cached && inForecastBounds(cached.latitude, cached.longitude, result.bounds)) {
          setStartBounds([[cached.longitude - 0.425, cached.latitude - 0.3], [cached.longitude + 0.425, cached.latitude + 0.3]]);
        }
        initialized.current = true;
      } catch (error) {
        if (active) setStatus(layer === "personal" && error instanceof ApiError && error.status === 401
          ? "Sign in from the Symptom Log tab to view your personalized asthma risk."
          : "Could not refresh the forecast. Tap to retry.");
      }
    }
    void load();
    const timer = setInterval(() => setRefresh(value => value + 1), 5 * 60 * 1000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [focused, refresh, loader, layer, userId]);

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
        if (layer !== "risk") setFrameFailure(null);
      } catch (error) {
        if (!active || (layer !== "risk" && error instanceof Error && error.name === "AbortError")) return;
        if (error instanceof ApiError && (error.status === 409 || error.status === 404)) {
          setRefresh(value => value + 1);
        } else setFrameFailure({ id: frameId, message: "Map layer unavailable. Tap to retry." });
      }
    }, cached ? 0 : 50);
    return () => { active = false; clearTimeout(timer); };
  }, [forecast, selected, focused, refresh, frameId, hoursAhead, loader, layer, frameCache]);

  const cellKey = `${forecast?.run_id}/${selected?.index}/${cellLocation?.latitude}/${cellLocation?.longitude}`;
  const showingSelected = displayed?.run_id === forecast?.run_id && displayed?.index === selected?.index;
  useEffect(() => {
    if (!focused || picking || !cellLocation || !forecast || !selected || (selected.available && !showingSelected)) return;
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const data = await api.mapCell(forecast.run_id, selected.index, cellLocation.latitude, cellLocation.longitude, layer, controller.signal);
        if (active) setCellState({key:cellKey,data,error:""});
      } catch (error) {
        if (!active) return;
        if (error instanceof ApiError && error.status === 409) setRefresh(value => value + 1);
        else setCellState({key:cellKey,data:null,error:"Cell details unavailable. Tap to retry."});
      }
    }, 80);
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [focused,picking,cellLocation,forecast,selected,showingSelected,layer,cellKey,cellRefresh]);

  async function chooseCoordinate(latitude: number, longitude: number) {
    if (selectionBusy) return;
    setSelectionBusy(true); setSelectionError("");
    try {
      const coverage = await api.locationCoverage(latitude,longitude);
      if (!alive.current) return;
      if (!coverage.in_georgia) throw new Error("Choose a grid cell within Georgia.");
      if (!coverage.forecast_supported) throw new Error("Choose a cell within the forecast coverage.");
      await storeDashboardPlace(userId ?? undefined,coordinatePlace(latitude,longitude,true));
      if (alive.current) { router.setParams({pickDashboard:undefined}); router.replace("/dashboard"); }
    } catch (error) {
      if (alive.current) setSelectionError(error instanceof Error ? error.message : "Could not select this location.");
    } finally { if (alive.current) setSelectionBusy(false); }
  }
  const clickCell = (latitude:number,longitude:number) => {
    if (selectionBusy || !forecast) return;
    const cell = forecastCellAt(latitude,longitude,forecast.bounds);
    setSelectionError(""); setCellState({key:"",data:null,error:""}); setCellLocation(cell);
    if (!cell) { setSelectionError("Choose a cell inside the shaded forecast area."); return; }
    if (picking) void chooseCoordinate(cell.latitude,cell.longitude);
  };
  const cellData = cellState.key === cellKey && (!selected?.available || showingSelected) ? cellState.data : null;
  const cellError = cellState.key === cellKey ? cellState.error : "";

  const pending = selected?.available && (!displayed || displayed.run_id !== forecast?.run_id || displayed.index !== selected.index);
  const visibleStatus = layer === "personal" && !userId ? "Sign in from the Symptom Log tab to view your personalized asthma risk." : !forecast ? status : !selected?.available ? "No data for this hour."
    : frameFailure?.id === frameId && pending ? frameFailure.message
    : status;

  const formatTime = (time: string) => new Date(time).toLocaleString("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
  const validLabel = selected ? pending && displayed?.run_id === forecast?.run_id
    ? `Showing ${formatTime(displayed.valid_at)} · Selected ${formatTime(selected.valid_at)}`
    : pending && layer !== "risk" ? `Loading ${formatTime(selected.valid_at)}…` : formatTime(selected.valid_at) : "";
  const descriptor = forecast?.layer ?? layers.find(item => item.id === layer) ?? GENERAL_RISK_LAYER;
  const personalNote = layer === "personal" && forecast?.personalization
    ? forecast.personalization.matched_logs === 0 ? "General baseline · No matched daily reports yet"
      : `${forecast.personalization.matched_logs} matched daily reports${forecast.personalization.still_learning ? " · Learning your triggers" : ""}${forecast.personalization.pending_logs ? ` · ${forecast.personalization.pending_logs} awaiting exposure data` : ""}`
    : "";
  const note = forecast ? [descriptor.description, descriptor.processing, personalNote, forecast.provisional && layer === "risk" ? "Development estimate" : "",
    forecast.stale ? "NASA run older than 24h" : "", "Coverage limited to shaded area"].filter(Boolean).join(" · ") : "";

  return <View style={styles.root}>
    <HeatmapView key={JSON.stringify(startBounds)} raster={raster} hoursAhead={hoursAhead} maxHours={Math.max(0, (forecast?.frames.length ?? 1) - 1)}
      onHoursAheadChange={changeHour} onRasterShown={rasterShown} userLocation={location} visualization="heat" startBounds={startBounds}
      gridStepDegrees={forecast?.grid_step_degrees}
      layer={descriptor} layers={layers} onLayerChange={changeLayer}
      loadingLabel={focused && !(layer === "personal" && !userId) &&
        (status === "Loading NASA forecast…" || (pending && frameFailure?.id !== frameId && !status))
        ? `Loading ${descriptor.label} layer…` : undefined}
      onCellPress={clickCell} selectionBounds={cellLocation?.bounds} selectionMode={picking}
      onCancelSelection={() => { if (!selectionBusy) { router.setParams({pickDashboard:undefined}); router.replace("/dashboard"); } }}
      status={selectionError || (selectionBusy ? "Selecting dashboard location…" : visibleStatus)} note={note} validLabel={validLabel} colors={forecast?.colors}
      onRetry={() => layer === "personal" && !userId ? router.push("/symptoms") : setRefresh(value => value + 1)} />
    {cellLocation && !picking && <MapCellPopup latitude={cellLocation.latitude} longitude={cellLocation.longitude}
      data={cellData} layer={descriptor} error={selectionError || cellError} busy={selectionBusy}
      onClose={() => setCellLocation(null)} onRetry={() => { setSelectionError(""); setCellState({key:"",data:null,error:""}); setCellRefresh(value=>value+1); }}
      onUse={() => void chooseCoordinate(cellLocation.latitude,cellLocation.longitude)} />}
  </View>;
}

const styles = StyleSheet.create({ root: { flex: 1, backgroundColor: "#f4f7f4" } });
