import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { DashboardRisk } from "../lib/api-client";
import { groupForecastInputs } from "../lib/dashboard-insights";

const NAMES: Record<string, string> = { o3: "Surface O₃", no2: "Surface NO₂", so2: "SO₂", pm25: "PM2.5",
  air_temperature: "Temperature", specific_humidity: "Specific humidity", surface_pressure: "Surface pressure",
  pbl_height: "Boundary-layer height", tropcol_no2: "Tropospheric NO₂ column" };

export default function ForecastDetailsPanel({ forecast, now }: { forecast: DashboardRisk | null; now: number }) {
  const [expanded, setExpanded] = useState(false);
  if (!forecast) return null;
  const timezone = forecast.timezone ?? "America/New_York";
  const format = (time: string, zone = timezone) => new Date(time).toLocaleString("en-US", {
    timeZone: zone, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
  const details = forecast.forecast_details;
  const groups = groupForecastInputs(details?.input_sources ?? []);
  const remaining = details ? Math.max(0, Math.floor((Date.parse(details.forecast_end) - now) / 3600000)) : null;
  return <View style={styles.panel}>
    <TouchableOpacity onPress={() => setExpanded(value => !value)} accessibilityRole="button"
      accessibilityLabel="NASA forecast details" accessibilityState={{ expanded }} style={styles.toggle}>
      <Ionicons name="information-circle-outline" size={15} color="#748178" />
      <View style={styles.summary}><Text style={styles.title}>NASA GEOS-CF · {format(forecast.issued_at)}</Text>
        <Text style={styles.small}>{details ? `Through ${format(details.forecast_end)} · ${remaining ? `~${remaining}h` : "Less than 1h"} remaining`
          : "Source horizon not recorded"}{forecast.stale ? " · Older than 24h" : ""}</Text></View>
      <Ionicons name={expanded ? "chevron-up" : "chevron-down"} size={13} color="#748178" />
    </TouchableOpacity>
    {expanded && <View style={styles.details}>
      <Text style={styles.small}>NASA initialization: {format(forecast.issued_at, "UTC")}</Text>
      <Text style={styles.small}>Run: {forecast.source_run}</Text>
      {details && <>
        <Text style={styles.small}>Source coverage: {format(details.forecast_start)} – {format(details.forecast_end)}</Text>
        <Text style={styles.small}>Dashboard: {details.displayed_hours} hourly intervals · {details.scored_hours} with scores{details.displayed_through ? ` · through ${format(details.displayed_through)}` : ""}</Text>
      </>}
      <Text style={styles.label}>{groups.length === 1 && groups[0].issuedAt ? "All recorded inputs share this initialization" : "Recorded forecast input runs"}</Text>
      {groups.length ? groups.map((group, i) => <View key={i} style={styles.inputGroup}>
        <Text style={styles.small}>{group.variables.map(name => NAMES[name] ?? name.replaceAll("_", " ")).join(" · ")}</Text>
        <Text style={styles.small}>{group.issuedAt ? format(group.issuedAt) : "Initialization time not recorded"}{group.run ? ` · ${group.run}` : ""}</Text>
      </View>) : <Text style={styles.small}>Per-variable run times were not recorded for this publication.</Text>}
      <Text style={styles.small}>Initialization is the source run time, not the download time. Risk also uses preceding historical analysis for rolling exposures.</Text>
    </View>}
  </View>;
}

const styles = StyleSheet.create({
  panel: { marginTop: 4, borderTopWidth: 1, borderTopColor: "#dfe7e1", paddingTop: 10 },
  toggle: { flexDirection: "row", alignItems: "center", gap: 7, paddingVertical: 5 },
  summary: { flex: 1, gap: 4 },
  title: { color: "#66766b", fontSize: 10, fontWeight: "600", lineHeight: 16 },
  small: { color: "#7a877e", fontSize: 10, lineHeight: 16 },
  details: { paddingLeft: 22, paddingTop: 7, gap: 6 },
  label: { color: "#66766b", fontSize: 10, fontWeight: "600", marginTop: 3 },
  inputGroup: { paddingLeft: 8, borderLeftWidth: 2, borderLeftColor: "#dce5df", gap: 2 },
});
