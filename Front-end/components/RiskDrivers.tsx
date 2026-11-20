import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { hourLabel, type RiskHour } from "../lib/hourly-risk";

const POLLUTANTS = {
  O3: { symbol: "O₃", name: "Ozone" },
  NO2: { symbol: "NO₂", name: "Nitrogen dioxide" },
  SO2: { symbol: "SO₂", name: "Sulfur dioxide" },
  PM25: { symbol: "PM₂.₅", name: "Fine particles" },
};
const number = (value: number) => value.toLocaleString("en-US", { maximumSignificantDigits: 3 });

export default function RiskDrivers({ hour, timezone, personalized }: {
  hour: RiskHour | undefined;
  timezone: string;
  personalized: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const drivers = hour?.drivers ?? [];
  const leader = drivers[0];
  const maxImpact = Math.max(1, ...drivers.map(driver => Math.abs(driver.impact)));
  const adjustment = hour?.score !== null && hour?.score !== undefined && hour.general_score !== null
    ? hour.score - hour.general_score : null;

  return <View style={styles.section}>
    <View style={styles.heading}>
      <View style={styles.icon}><Ionicons name="layers-outline" size={17} color="#52715d" /></View>
      <View style={styles.headingText}>
        <Text style={styles.title}>What’s driving the score?</Text>
        <Text style={styles.subtitle}>{hour ? hourLabel(hour.valid_at, timezone, true) : "Select an hour to explore"}</Text>
      </View>
    </View>
    {!hour || hour.score === null || !drivers.length ? <Text style={styles.note}>
      {!hour || hour.score === null ? "Pollutant drivers are unavailable for this hour because its forecast is missing."
        : "Detailed pollutant drivers aren’t available for this forecast."}
    </Text> : <>
      <Text style={styles.headline}>{leader && leader.impact > 0
        ? `${POLLUTANTS[leader.pollutant].name} has the largest upward influence in this comparison.`
        : "Individual pollutant comparisons show little upward movement at this hour."}</Text>

      {personalized && adjustment !== null && <View style={styles.personal}>
        <View style={styles.personalMetric}><Text style={styles.metricLabel}>General</Text><Text style={styles.metricValue}>{hour.general_score}</Text></View>
        <Text style={styles.math}>+</Text>
        <View style={styles.personalMetric}><Text style={styles.metricLabel}>Your adjustment</Text><Text style={styles.metricValue}>{adjustment < 0 ? `(${adjustment})` : adjustment}</Text></View>
        <Text style={styles.math}>=</Text>
        <View style={styles.personalMetric}><Text style={styles.metricLabel}>Your score</Text><Text style={styles.metricValue}>{hour.score}</Text></View>
      </View>}

      <View style={styles.list}>
        {drivers.map(driver => {
          const pollutant = POLLUTANTS[driver.pollutant];
          const direction = driver.impact > 0 ? "Raises score" : driver.impact < 0 ? "Lowers score" : "Little change";
          const color = driver.impact > 0 ? "#a46b35" : driver.impact < 0 ? "#39845c" : "#839187";
          const impact = driver.impact === 0 ? "≈ 0" : `≈ ${driver.impact > 0 ? "+" : "−"}${number(Math.abs(driver.impact))}`;
          return <View key={driver.pollutant} style={styles.driver} accessible
            accessibilityLabel={`${pollutant.name}. Model exposure ${number(driver.exposure)} ${driver.unit}. Reference ${number(driver.reference)} ${driver.unit}. ${direction}, approximately ${number(Math.abs(driver.impact))} score points compared with its reference level.`}>
            <View style={styles.driverRow}>
              <View style={styles.pollutant}>
                <Text style={styles.symbol}>{pollutant.symbol}</Text>
                <Text style={styles.name}>{pollutant.name}</Text>
              </View>
              <View style={styles.effect}>
                <Text style={[styles.impact, { color }]}>{impact}<Text style={styles.points}> pts</Text></Text>
                <Text style={styles.direction}>{direction}</Text>
              </View>
            </View>
            <View style={styles.track}><View style={[styles.fill, { width: `${Math.abs(driver.impact) / maxImpact * 100}%`, backgroundColor: color }]} /></View>
            <Text style={styles.exposure}>Model exposure {number(driver.exposure)} {driver.unit} · Reference {number(driver.reference)} {driver.unit}</Text>
          </View>;
        })}
      </View>
      <Text style={styles.note}>Each comparison changes one pollutant to its reference level. These effects are independent and do not add up to the score.</Text>
      <TouchableOpacity onPress={() => setExpanded(value => !value)} accessibilityRole="button"
        accessibilityLabel="How to read the score drivers" accessibilityState={{ expanded }} aria-expanded={expanded} style={styles.explainButton}>
        <Ionicons name="information-circle-outline" size={15} color="#52715d" />
        <Text style={styles.explainText}>How to read this</Text>
        <Ionicons name={expanded ? "chevron-up" : "chevron-down"} size={12} color="#52715d" />
      </TouchableOpacity>
      {expanded && <View style={styles.details}>
        <Text style={styles.detailText}>The score ranks modeled risk against a historical reference. It is not a percentage chance of symptoms. Reference exposure levels come from the model’s fixed historical dataset; they are not regulatory limits.</Text>
        <Text style={styles.detailText}>Exposure values combine recent daily metrics across three days: ozone uses the daily highest 8-hour average, nitrogen dioxide and sulfur dioxide use daily hourly maxima, and fine particles use a daily average. These are not individual hourly readings.</Text>
        <Text style={styles.detailText}>“Raises” or “lowers” compares the forecast with the same model after setting just that pollutant to its reference exposure. The other exposures stay fixed. Values near 0 or 100 can hide an individual pollutant’s influence.</Text>
        {personalized && <Text style={styles.detailText}>Your adjustment uses matched, finalized symptom reports and model confidence. Pollutant comparisons recalculate that adjustment too; they are model explanations, not proof of an individual trigger.</Text>}
        <Text style={styles.detailText}>The model and reference calibration are still under development.</Text>
      </View>}
    </>}
  </View>;
}

const styles = StyleSheet.create({
  section: { borderTopWidth: 1, borderTopColor: "#e8eee9", marginTop: 20, paddingTop: 19 },
  heading: { flexDirection: "row", alignItems: "center", gap: 9 },
  headingText: { flex: 1 },
  icon: { width: 34, height: 34, borderRadius: 11, backgroundColor: "#eef4ee", alignItems: "center", justifyContent: "center" },
  title: { color: "#2d4535", fontSize: 16, fontWeight: "700", letterSpacing: -0.3 },
  subtitle: { color: "#839187", fontSize: 10, marginTop: 3 },
  headline: { color: "#5d7564", fontSize: 12, lineHeight: 19, marginTop: 13 },
  personal: { flexDirection: "row", alignItems: "center", gap: 5, padding: 12, borderRadius: 12, backgroundColor: "#f0f6f0", marginTop: 12 },
  personalMetric: { flex: 1, alignItems: "center" },
  metricLabel: { color: "#7c8e81", fontSize: 9, textAlign: "center" },
  metricValue: { color: "#2d6a4f", fontSize: 19, fontWeight: "700", marginTop: 4 },
  math: { color: "#a0b1a4", fontSize: 14 },
  list: { marginTop: 15, gap: 16 },
  driver: { gap: 6 },
  driverRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  pollutant: { flexDirection: "row", alignItems: "baseline", gap: 7, flex: 1, flexWrap: "wrap" },
  symbol: { color: "#304a39", fontSize: 14, fontWeight: "800" },
  name: { color: "#7b8c80", fontSize: 10 },
  effect: { alignItems: "flex-end" },
  impact: { fontSize: 14, fontWeight: "700" },
  points: { fontSize: 9, fontWeight: "500", color: "#8d9a91" },
  direction: { fontSize: 8, color: "#8d9a91", marginTop: 2 },
  track: { height: 4, borderRadius: 2, backgroundColor: "#edf1ed", overflow: "hidden" },
  fill: { height: "100%", borderRadius: 2, opacity: 0.65 },
  exposure: { color: "#8a998f", fontSize: 9, lineHeight: 14 },
  note: { color: "#829287", fontSize: 10, lineHeight: 16, marginTop: 14 },
  explainButton: { flexDirection: "row", alignItems: "center", gap: 6, alignSelf: "flex-start", paddingVertical: 12 },
  explainText: { color: "#52715d", fontSize: 11, fontWeight: "600" },
  details: { padding: 12, borderRadius: 12, backgroundColor: "#f5f8f4", gap: 8 },
  detailText: { color: "#718276", fontSize: 11, lineHeight: 18 },
});
