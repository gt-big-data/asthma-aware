import LoadingFeedback from "./LoadingFeedback";
import { Ionicons } from "@expo/vector-icons";
import { useMemo, useRef, useState, type ComponentRef } from "react";
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { DashboardRisk } from "../lib/api-client";
import RiskDrivers from "./RiskDrivers";
import { hourLabel, localDay, riskColor, riskLabel, scoreRange, windowHours } from "../lib/hourly-risk";

const CHART_HEIGHT = 144;

export default function HourlyRiskTimeline({ forecast, loading, onRetry }: {
  forecast: DashboardRisk | null;
  loading: boolean;
  onRetry: () => void;
}) {
  const [window, setWindow] = useState("next");
  const [chartType, setChartType] = useState<"line" | "bar">("line");
  const [selectedTime, setSelectedTime] = useState<string | null>(null);
  const [width, setWidth] = useState(0);
  const scroller = useRef<ComponentRef<typeof ScrollView>>(null);
  const timezone = forecast?.timezone ?? "America/New_York";
  const hours = useMemo(() => forecast?.hourly ?? [], [forecast]);
  const visible = useMemo(() => windowHours(hours, window, timezone), [hours, window, timezone]);
  const selectedIndex = Math.max(0, visible.findIndex(hour => hour.valid_at === selectedTime));
  const selected = visible[selectedIndex];
  const range = scoreRange(visible);
  const columnWidth = Math.max(34, (width - 4) / Math.max(1, visible.length));
  const zoneLabel = hours.length ? new Intl.DateTimeFormat("en-US", {
    timeZone: timezone, timeZoneName: "short",
  }).formatToParts(new Date(hours[0].valid_at)).find(part => part.type === "timeZoneName")?.value : "";

  function choose(index: number) {
    const hour = visible[index];
    if (!hour) return;
    setSelectedTime(hour.valid_at);
    scroller.current?.scrollTo({ x: Math.max(0, index * columnWidth - width / 2 + columnWidth / 2), animated: true });
  }

  return <View style={styles.card}>
    <View style={styles.heading}>
      <View style={styles.headingText}>
        <Text style={styles.eyebrow}>PLAN YOUR DAY</Text>
        <Text style={styles.title}>Your hourly outlook</Text>
        <Text style={styles.subtitle}>See how respiratory risk changes through the day.</Text>
        {forecast && <Text style={styles.metadata}>
          {forecast.personalization?.matched_logs ? "Personalized" : "General population"} forecast
          {forecast.placeholder ? " · Development estimate" : ""}{forecast.stale ? " · Older NASA run" : ""}
        </Text>}
      </View>
      <View style={styles.clock}><Ionicons name="time-outline" size={23} color="#2d6a4f" /></View>
    </View>

    {hours.length > 0 ? <>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabs}>
        {[{ key: "next", label: "Next 24 hours" }, ...(forecast?.days ?? []).map((day, i) => ({
          key: day.date, label: i === 0 ? "Today" : new Date(`${day.date}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" }),
        }))].map(tab => {
          const disabled = windowHours(hours, tab.key, timezone).length === 0;
          return <TouchableOpacity key={tab.key} disabled={disabled} accessibilityRole="button"
            accessibilityLabel={`Show ${tab.label} hourly forecast`} accessibilityState={{ selected: window === tab.key, disabled }}
            onPress={() => { setWindow(tab.key); setSelectedTime(null); scroller.current?.scrollTo({ x: 0, animated: false }); }}
            style={[styles.tab, window === tab.key && styles.activeTab, disabled && styles.disabled]}>
            <Text style={[styles.tabText, window === tab.key && styles.activeTabText]}>{tab.label}</Text>
          </TouchableOpacity>;
        })}
      </ScrollView>

      <View style={styles.readingRow}>
        <View style={styles.reading}>
          <Text style={styles.selectedTime}>{selectedIndex === 0 && selected?.valid_at === hours[0]?.valid_at
            ? "Current hour" : "Selected hour"}</Text>
          <Text style={styles.selectedDate}>{selected ? hourLabel(selected.valid_at, timezone, true) : "No hours available"}</Text>
          <View style={styles.scoreRow}>
            <Text style={styles.score}>{selected?.score ?? "—"}<Text style={styles.outOf}> / 100</Text></Text>
            {selected?.score !== null && selected?.score !== undefined ? <View style={[styles.riskPill, { backgroundColor: `${riskColor(selected.score)}14` }]}>
              <View style={[styles.dot, { backgroundColor: riskColor(selected.score) }]} />
              <Text style={[styles.riskText, { color: riskColor(selected.score) }]}>{riskLabel(selected.score)} risk</Text>
            </View> : <Text style={styles.subtitle}>No forecast data</Text>}
          </View>
        </View>
        <View style={styles.navigation}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Previous forecast hour" disabled={selectedIndex === 0}
            onPress={() => choose(selectedIndex - 1)} style={[styles.arrow, selectedIndex === 0 && styles.disabled]}>
            <Ionicons name="chevron-back" size={18} color="#2d6a4f" />
          </TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Next forecast hour" disabled={selectedIndex >= visible.length - 1}
            onPress={() => choose(selectedIndex + 1)} style={[styles.arrow, selectedIndex >= visible.length - 1 && styles.disabled]}>
            <Ionicons name="chevron-forward" size={18} color="#2d6a4f" />
          </TouchableOpacity>
        </View>
      </View>

      <View style={styles.chartTools}>
        <Text style={styles.chartLabel}>Risk score</Text>
        <View style={styles.chartToggle} accessibilityRole="radiogroup" accessibilityLabel="Chart type">
          {(["line", "bar"] as const).map(type => <TouchableOpacity key={type}
            accessibilityRole="radio" accessibilityLabel={`${type === "line" ? "Line" : "Bar"} chart`}
            accessibilityState={{ checked: chartType === type }} aria-checked={chartType === type} onPress={() => setChartType(type)}
            style={[styles.chartToggleButton, chartType === type && styles.chartToggleActive]}>
            <Ionicons name={type === "line" ? "analytics-outline" : "bar-chart-outline"} size={13}
              color={chartType === type ? "#2d6a4f" : "#809186"} />
            <Text style={[styles.chartToggleText, chartType === type && styles.chartToggleActiveText]}>
              {type === "line" ? "Line" : "Bar"}
            </Text>
          </TouchableOpacity>)}
        </View>
      </View>
      <View style={styles.chartRow}>
        <View style={styles.yAxis}><Text style={styles.axisText}>100</Text><Text style={styles.axisText}>50</Text><Text style={styles.axisText}>0</Text></View>
        <View style={styles.chartViewport} onLayout={event => setWidth(event.nativeEvent.layout.width)}>
          <View style={styles.guides} pointerEvents="none">{[0, 50, 100].map(value => <View key={value} style={styles.guide} />)}</View>
          <ScrollView ref={scroller} horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chartContent}>
            {visible.map((hour, index) => {
              const active = index === selectedIndex;
              const first = index === 0 && hour.valid_at === hours[0]?.valid_at;
              const startOfDay = index === 0 || localDay(hour.valid_at, timezone) !== localDay(visible[index - 1].valid_at, timezone);
              const date = new Date(hour.valid_at).toLocaleDateString("en-US", { weekday: "short", timeZone: timezone });
              return <TouchableOpacity key={hour.valid_at} accessibilityRole="button"
                accessibilityLabel={`${hourLabel(hour.valid_at, timezone, true)}, ${hour.score === null ? "forecast unavailable" : `risk ${hour.score} out of 100, ${riskLabel(hour.score)}`}`}
                accessibilityState={{ selected: active }} onPress={() => setSelectedTime(hour.valid_at)}
                activeOpacity={0.8} style={[styles.column, { width: columnWidth }]}>
                <View style={[styles.barArea, active && styles.activeColumn]}>
                  {active && <View style={styles.selectionLine} />}
                  {hour.score === null ? <Text style={styles.missing}>—</Text> : chartType === "bar" && <View style={[
                    styles.bar, { height: Math.max(3, hour.score / 100 * CHART_HEIGHT), backgroundColor: riskColor(hour.score), opacity: active ? 1 : 0.68 },
                  ]} />}
                  {chartType === "bar" && active && hour.score !== null && <View style={[styles.barDot, { bottom: Math.max(3, hour.score / 100 * CHART_HEIGHT) - 4, backgroundColor: riskColor(hour.score) }]} />}
                </View>
                <Text style={[styles.hour, active && styles.activeHour]}>{first ? "Now" : hourLabel(hour.valid_at, timezone).replace(" ", "")}</Text>
                <Text style={styles.dayMarker}>{startOfDay ? date : " "}</Text>
              </TouchableOpacity>;
            })}
            {chartType === "line" && <View pointerEvents="none" accessible={false}
              style={[styles.lineOverlay, { width: visible.length * columnWidth }]}>
              {visible.slice(0, -1).map((hour, index) => {
                const next = visible[index + 1];
                // Preserve gaps instead of connecting across unavailable hours.
                if (hour.score === null || next.score === null
                  || new Date(next.valid_at).getTime() - new Date(hour.valid_at).getTime() !== 3600000) return null;
                const x1 = (index + 0.5) * columnWidth;
                const x2 = x1 + columnWidth;
                const y1 = CHART_HEIGHT * (1 - hour.score / 100);
                const y2 = CHART_HEIGHT * (1 - next.score / 100);
                const length = Math.hypot(columnWidth, y2 - y1);
                return <View key={hour.valid_at} style={[styles.lineSegment, {
                  left: (x1 + x2 - length) / 2, top: (y1 + y2 - 3) / 2, width: length,
                  backgroundColor: riskColor((hour.score + next.score) / 2),
                  transform: [{ rotate: `${Math.atan2(y2 - y1, columnWidth)}rad` }],
                }]} />;
              })}
              {visible.map((hour, index) => hour.score === null ? null : <View key={hour.valid_at}
                style={[styles.linePoint, index === selectedIndex && styles.selectedPoint, {
                  left: (index + 0.5) * columnWidth - (index === selectedIndex ? 5 : 3),
                  top: CHART_HEIGHT * (1 - hour.score / 100) - (index === selectedIndex ? 5 : 3),
                  backgroundColor: riskColor(hour.score),
                }]} />)}
            </View>}
          </ScrollView>
        </View>
      </View>
      <View style={styles.footer}>
        <View style={styles.legend}>{[20, 50, 85].map(score => <View key={score} style={styles.legendItem}>
          <View style={[styles.dot, { backgroundColor: riskColor(score) }]} /><Text style={styles.legendText}>{riskLabel(score)}</Text>
        </View>)}</View>
        <Text style={styles.hint}>{visible.length * columnWidth > width + 4 ? "Swipe for more · " : "Tap an hour · "}{zoneLabel}</Text>
      </View>
      <View style={styles.summary}>
        <Ionicons name="pulse-outline" size={15} color="#567361" />
        <Text style={styles.summaryText}>{range ? `Forecast range ${range.min}–${range.max} · ${visible.filter(hour => hour.score !== null).length} available hours`
          : "No usable scores for this period."}</Text>
      </View>
      <RiskDrivers hour={selected} timezone={timezone} personalized={!!forecast?.personalization?.matched_logs} />
    </> : <View style={styles.empty}>
      {loading?<View style={{width:"100%",maxWidth:360}}><LoadingFeedback label="Loading your hourly forecast" skeleton/></View>
        :<Ionicons name="cloud-offline-outline" size={28} color="#82958a"/>}
      {!loading&&<Text style={styles.emptyTitle}>Hourly forecast unavailable</Text>}
      <Text style={styles.emptyText}>{loading ? "Finding the latest available hours for your location." : "There isn’t a usable forecast for this location right now."}</Text>
      {!loading && <TouchableOpacity onPress={onRetry} accessibilityRole="button" style={styles.retry}><Text style={styles.retryText}>Try again</Text></TouchableOpacity>}
    </View>}
  </View>;
}

const styles = StyleSheet.create({
  card: { backgroundColor: "#fff", borderRadius: 22, borderWidth: 1, borderColor: "#dce5de", padding: 18, overflow: "hidden" },
  heading: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 12 },
  headingText: { flex: 1 },
  eyebrow: { fontSize: 9, fontWeight: "800", letterSpacing: 1.8, color: "#52715d", marginBottom: 6 },
  title: { fontSize: 22, fontWeight: "700", color: "#1f2d25", letterSpacing: -0.6 },
  subtitle: { fontSize: 12, lineHeight: 18, color: "#6a7b70", marginTop: 4 },
  metadata: { fontSize: 10, lineHeight: 16, color: "#7e8069", marginTop: 7 },
  clock: { width: 44, height: 44, backgroundColor: "#eef5ef", borderRadius: 15, alignItems: "center", justifyContent: "center" },
  tabs: { gap: 6, paddingVertical: 18 },
  tab: { borderRadius: 18, paddingVertical: 8, paddingHorizontal: 13, backgroundColor: "#f3f6f3" },
  activeTab: { backgroundColor: "#2d6a4f" },
  tabText: { fontSize: 11, fontWeight: "600", color: "#637669" },
  activeTabText: { color: "#fff" },
  disabled: { opacity: 0.35 },
  readingRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 23 },
  reading: { flex: 1 },
  selectedTime: { fontSize: 10, color: "#6a7b70", fontWeight: "700", textTransform: "uppercase", letterSpacing: 1 },
  selectedDate: { fontSize: 12, fontWeight: "600", color: "#435e4d", marginTop: 5 },
  scoreRow: { flexDirection: "row", alignItems: "center", gap: 12, flexWrap: "wrap", marginTop: 6 },
  score: { fontSize: 34, fontWeight: "800", color: "#253b2d", letterSpacing: -1.2 },
  outOf: { fontSize: 12, fontWeight: "500", color: "#849488", letterSpacing: 0 },
  riskPill: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 9, paddingVertical: 5, borderRadius: 12 },
  riskText: { fontSize: 11, fontWeight: "700" },
  dot: { width: 6, height: 6, borderRadius: 3 },
  navigation: { flexDirection: "row", gap: 6 },
  arrow: { width: 32, height: 32, borderRadius: 11, borderWidth: 1, borderColor: "#e0e9e2", alignItems: "center", justifyContent: "center" },
  chartTools: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 14 },
  chartLabel: { fontSize: 10, color: "#809186", fontWeight: "600" },
  chartToggle: { flexDirection: "row", padding: 3, borderRadius: 12, backgroundColor: "#f1f5f1", gap: 2 },
  chartToggleButton: { flexDirection: "row", alignItems: "center", gap: 5, paddingVertical: 7, paddingHorizontal: 11, borderRadius: 9 },
  chartToggleActive: { backgroundColor: "#fff" },
  chartToggleText: { fontSize: 10, fontWeight: "600", color: "#809186" },
  chartToggleActiveText: { color: "#2d6a4f" },
  lineOverlay: { position: "absolute", left: 2, top: 0, height: CHART_HEIGHT },
  lineSegment: { position: "absolute", height: 3, borderRadius: 2 },
  linePoint: { position: "absolute", width: 6, height: 6, borderRadius: 3, borderWidth: 1, borderColor: "#fff" },
  selectedPoint: { width: 10, height: 10, borderRadius: 5, borderWidth: 2 },
  chartRow: { flexDirection: "row" },
  yAxis: { height: CHART_HEIGHT + 2, justifyContent: "space-between", width: 27, marginRight: 2 },
  axisText: { fontSize: 9, color: "#8a998f" },
  chartViewport: { flex: 1, minWidth: 0 },
  guides: { position: "absolute", top: 0, left: 0, right: 0, height: CHART_HEIGHT, justifyContent: "space-between" },
  guide: { height: 1, backgroundColor: "#eaf0eb" },
  chartContent: { paddingHorizontal: 2 },
  column: { alignItems: "center" },
  barArea: { height: CHART_HEIGHT, width: "100%", alignItems: "center", justifyContent: "flex-end", borderTopLeftRadius: 8, borderTopRightRadius: 8 },
  activeColumn: { backgroundColor: "#edf4ed" },
  selectionLine: { position: "absolute", top: 0, bottom: 0, width: 1, backgroundColor: "#c6d8c9" },
  bar: { width: "52%", maxWidth: 36, borderTopLeftRadius: 5, borderTopRightRadius: 5 },
  barDot: { width: 9, height: 9, borderRadius: 5, borderWidth: 2, borderColor: "#fff", position: "absolute" },
  missing: { color: "#a2afa5", fontSize: 16, marginBottom: 3 },
  hour: { color: "#809186", fontSize: 9, marginTop: 10, fontWeight: "500" },
  activeHour: { color: "#2d6a4f", fontWeight: "800" },
  dayMarker: { fontSize: 8, color: "#9ba89f", marginTop: 4, marginBottom: 3 },
  footer: { flexDirection: "row", justifyContent: "space-between", gap: 8, flexWrap: "wrap", marginTop: 14 },
  legend: { flexDirection: "row", gap: 12 },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 4 },
  legendText: { fontSize: 9, color: "#6a7b70" },
  hint: { fontSize: 9, color: "#899a8e" },
  summary: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: "#f4f7f3", borderRadius: 10, padding: 9, marginTop: 14 },
  summaryText: { fontSize: 10, color: "#627768", flex: 1 },
  empty: { alignItems: "center", paddingVertical: 36, gap: 10 },
  emptyTitle: { color: "#435e4d", fontWeight: "600", fontSize: 14 },
  emptyText: { color: "#809186", fontSize: 12, textAlign: "center", maxWidth: 300, lineHeight: 18 },
  retry: { backgroundColor: "#eef5ef", borderRadius: 16, paddingVertical: 9, paddingHorizontal: 16, marginTop: 6 },
  retryText: { color: "#2d6a4f", fontWeight: "700", fontSize: 12 },
});
