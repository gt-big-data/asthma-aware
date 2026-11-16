import { useEffect, useMemo, useRef, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { getUserLocation } from "../constants/userLocation";
import { forecastHours, forecastPixels } from "../lib/forecast";
import { useAuth } from "../providers/AuthProvider";
import { useForecast } from "../providers/ForecastProvider";
import HeatmapView, { type MapBounds } from "./HeatmapView";
import { AuthForm } from "./logging/AuthForm";
import { LocationPicker } from "./logging/LocationPicker";
import { Button, Card, Loading, Notice, colors, styles } from "./logging/ui";

export function ForecastScreen({ map = false }: { map?: boolean }) {
  const auth = useAuth();
  const { point, data, loading, error, now, selectPoint, refresh } = useForecast();
  const insets = useSafeAreaInsets();
  const [picker, setPicker] = useState(false);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const hours = useMemo(() => data ? forecastHours(data, now) : [], [data, now]);
  const hour = hours.find((value) => value.timestamp === selected) ?? hours[0];
  const pixels = useMemo(() => data && hour ? forecastPixels(data, hour) : [], [data, hour]);
  const bounds: MapBounds | null = data ? [
    [data.grid.west - .01, data.grid.south - .01],
    [data.grid.east + .01, data.grid.north + .01],
  ] : null;

  async function locate() {
    setLocating(true); setLocationError(null);
    try {
      const result = await getUserLocation();
      if (!active.current) return;
      if (result) selectPoint(result);
      else setLocationError("Location unavailable. Choose a location instead.");
    } catch {
      if (active.current) setLocationError("Location unavailable. Choose a location instead.");
    } finally { if (active.current) setLocating(false); }
  }
  return <ScrollView style={styles.screen} keyboardShouldPersistTaps="handled"
    contentContainerStyle={[styles.content, { paddingTop: 24 + insets.top }]}>
    <View style={{ gap: 6 }}><Text style={styles.eyebrow}>ASTHMAAWARE</Text>
      <Text style={styles.title}>{map ? "Forecast map" : "Your forecast"}</Text>
      <Notice>NASA pollution forecasts with a temporary development score.</Notice></View>
    {auth.error && <Notice error>{auth.error}</Notice>}
    {auth.loading ? <Loading title="Restoring your session…" /> : !auth.session ? <AuthForm /> : <>
      <Card>
        <Text style={styles.heading}>Forecast location</Text>
        {point ? <Notice>{point.latitude.toFixed(4)}, {point.longitude.toFixed(4)}</Notice>
          : <Notice>Choose a place in Georgia. Coverage depends on the latest published NASA cells.</Notice>}
        <Button secondary title={locating ? "Finding your location…" : "Use current location"} disabled={locating}
          onPress={() => void locate()} />
        <Button secondary title="Choose a location" onPress={() => setPicker(true)} />
        {!point && <Button secondary title="View Atlanta" onPress={() => selectPoint({ latitude: 33.75, longitude: -84.39 })} />}
        {locationError && <Notice error>{locationError}</Notice>}
      </Card>
      {loading && <Card><Loading title="Loading the published forecast…" /></Card>}
      {error && <Card><Text style={styles.heading}>Forecast unavailable</Text><Notice error>{error}</Notice>
        <Notice>No demonstration values have been substituted. Try another location or refresh after a new forecast is published.</Notice>
        <Button title="Try again" onPress={refresh} disabled={loading} /></Card>}
      {data && !hour && <Card><Notice error>This forecast has expired or does not cover the current hour.</Notice>
        <Button title="Refresh forecast" onPress={refresh} disabled={loading} /></Card>}
      {data && hour && <>
        <Card>
          <Text style={styles.heading}>Development environmental score</Text>
          <Text accessibilityLabel={`Environmental score ${hour.score} out of 100`}
            style={{ fontSize: 48, fontWeight: "700", color: colors.accent }}>{hour.score}<Text style={{ fontSize: 18 }}> / 100</Text></Text>
          <Notice>{data.notice}</Notice>
          <Text style={styles.label}>{new Date(hour.timestamp).toLocaleString()} (device time)</Text>
          <View style={{ gap: 6 }}>
            <Text style={styles.note}>PM2.5: {hour.values.pm25.toFixed(2)} µg/m³</Text>
            <Text style={styles.note}>NO₂: {hour.values.no2.toFixed(2)} ppb</Text>
            <Text style={styles.note}>O₃: {hour.values.o3.toFixed(2)} ppb</Text>
          </View>
          <Notice>Each pollutant has equal weight after normalization. This score is not personalized from your symptom logs.</Notice>
        </Card>
        <View style={{ gap: 10 }}><Text style={styles.heading}>Available forecast hours</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
            {hours.map((item) => <Pressable key={item.timestamp} accessibilityRole="button"
              accessibilityState={{ selected: item.timestamp === hour.timestamp }} onPress={() => setSelected(item.timestamp)}
              style={{ padding: 12, minWidth: 90, borderRadius: 12, borderWidth: 1, borderColor: colors.border,
                backgroundColor: item.timestamp === hour.timestamp ? colors.accent : colors.card }}>
              <Text style={{ color: item.timestamp === hour.timestamp ? "white" : colors.text }}>
                {new Date(item.timestamp).toLocaleString(undefined, { weekday: "short", hour: "numeric" })}{"\n"}{item.score} / 100
              </Text></Pressable>)}
          </ScrollView>
        </View>
        {map && bounds && <>
          <View style={{ height: 460, overflow: "hidden", borderRadius: 18 }}>
            <HeatmapView key={`${data.run_id}:${data.location.native_lat}:${data.location.native_lon}`}
              points={pixels} userLocation={point} locationLabel="Forecast location" startBounds={bounds} visualization="cells" showTimeline={false}
              hoursAhead={hours.indexOf(hour)} onHoursAheadChange={() => {}} />
          </View>
          <Notice>Shown area: one published native NASA cell. Approximately 1 km pixels copy its value unchanged; they do not add local forecast detail.</Notice>
        </>}
        <Card><Text style={styles.heading}>Forecast source</Text>
          <Notice>{data.source}{"\n"}Run: {data.run_id}{"\n"}Initialized: {new Date(data.initialized_at).toLocaleString()}
            {"\n"}Native center: {data.location.native_lat}, {data.location.native_lon}{"\n"}Display pixel: {data.location.pixel_id}</Notice>
          <Button secondary title="Refresh forecast" onPress={refresh} disabled={loading} />
        </Card>
      </>}
    </>}
    {picker && <LocationPicker initial={point} onCancel={() => setPicker(false)} onSelect={(value) => {
      selectPoint(value); setPicker(false); setLocationError(null);
    }} />}
  </ScrollView>;
}
