import { Ionicons } from "@expo/vector-icons";
import { useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";
import type { DashboardRisk } from "../lib/api-client";
import { lowerRiskPeriods } from "../lib/dashboard-insights";
import { localDay, riskColor, riskLabel } from "../lib/hourly-risk";

export default function LowerRiskPeriods({ forecast, loading, now }: { forecast: DashboardRisk | null; loading: boolean; now: number }) {
  const insight = useMemo(() => lowerRiskPeriods(forecast?.hourly ?? [], now), [forecast, now]);
  const timezone = forecast?.timezone ?? "America/New_York";
  const day = (time: string) => localDay(time, timezone) === localDay(new Date(now).toISOString(), timezone) ? "Today"
    : new Date(time).toLocaleDateString("en-US", { timeZone: timezone, weekday: "short", month: "short", day: "numeric" });
  const clock = (time: string) => new Date(time).toLocaleTimeString("en-US", { timeZone: timezone, hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  return <View style={styles.card}>
    <View style={styles.heading}><View style={styles.icon}><Ionicons name="leaf-outline" size={20} color="#2d6a4f" /></View>
      <View style={styles.headingText}><Text style={styles.title}>Lower-risk periods</Text>
        <Text style={styles.subtitle}>Plan ahead · Next 48 hours</Text></View></View>
    {insight.periods.length ? <>
      <Text style={styles.caption}>{forecast?.personalization?.matched_logs ? "Personalized" : "General population"} forecast · {insight.availableHours} available hours</Text>
      {insight.periods.map(period => <View key={period.start} style={styles.period}>
        <View style={styles.periodMain}>
          <Text style={styles.day}>{day(period.start)}</Text>
          <Text style={styles.time}>{Date.parse(period.start) <= now + 60000 ? "Now" : clock(period.start)} – {localDay(period.end, timezone) !== localDay(period.start, timezone) ? `${day(period.end)}, ` : ""}{clock(period.end)}</Text>
          <Text style={styles.caption}>Score {period.min === period.peak ? period.peak : `${period.min}–${period.peak}`} / 100</Text>
        </View>
        <View style={[styles.badge, { backgroundColor: `${riskColor(period.peak)}14` }]}>
          <Text style={[styles.badgeText, { color: riskColor(period.peak) }]}>{riskLabel(period.peak)} risk</Text>
        </View>
      </View>)}
      <Text style={styles.footnote}>Periods last at least 2 hours, with low scores or a noticeable drop. Lower can still mean high risk.</Text>
    </> : <Text style={styles.empty}>{loading ? "Finding lower-risk periods…" : !forecast || !insight.availableHours
      ? "Lower-risk periods will appear when hourly scores are available."
      : "No distinct lower-risk period lasting 2 hours is forecast in the next 48 hours."}</Text>}
  </View>;
}

const styles = StyleSheet.create({
  card: { backgroundColor: "#fff", borderRadius: 18, padding: 16, borderWidth: 1, borderColor: "#e1e8e3", gap: 10 },
  heading: { flexDirection: "row", alignItems: "center", gap: 10 },
  headingText: { flex: 1 },
  icon: { width: 38, height: 38, borderRadius: 12, backgroundColor: "#edf5ef", alignItems: "center", justifyContent: "center" },
  title: { fontSize: 16, fontWeight: "700", color: "#24382b" },
  subtitle: { fontSize: 11, color: "#748178", marginTop: 3 },
  caption: { fontSize: 11, color: "#66766b", lineHeight: 16 },
  period: { borderTopWidth: 1, borderTopColor: "#edf1ed", paddingTop: 10, flexDirection: "row", alignItems: "center", gap: 10 },
  periodMain: { flex: 1, gap: 3 },
  day: { fontSize: 12, fontWeight: "700", color: "#34473b" },
  time: { fontSize: 13, fontWeight: "500", color: "#24382b", lineHeight: 19 },
  badge: { borderRadius: 12, paddingVertical: 5, paddingHorizontal: 9 },
  badgeText: { fontSize: 10, fontWeight: "700" },
  footnote: { fontSize: 10, color: "#748178", lineHeight: 15 },
  empty: { fontSize: 12, color: "#66766b", lineHeight: 19 },
});
