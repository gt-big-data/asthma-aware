import { useState } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  SafeAreaView,
  Platform,
} from "react-native";
import HeatmapView from "../components/HeatmapView";
import {
  VARIABLES,
  TIME_STEPS,
  HEATMAP_DATA,
  Variable,
  TimeStep,
} from "../constants/data";

export default function Index() {
  const [activeVariable, setActiveVariable] = useState<Variable>(VARIABLES[0]);
  const [activeTimeStep, setActiveTimeStep] = useState<TimeStep>(TIME_STEPS[0]);

  const points = HEATMAP_DATA[activeVariable][activeTimeStep];

  return (
    <View style={styles.root}>
      {/* ── Full-screen map ── */}
      <HeatmapView points={points} />

      <SafeAreaView style={styles.overlay} pointerEvents="box-none">
        {/* ── Variable selector (top) ── */}
        <View style={styles.topBar} pointerEvents="auto">
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.pillRow}
          >
            {VARIABLES.map((v) => {
              const active = v === activeVariable;
              return (
                <TouchableOpacity
                  key={v}
                  style={[styles.pill, active && styles.pillActive]}
                  onPress={() => setActiveVariable(v)}
                  activeOpacity={0.75}
                >
                  <Text style={[styles.pillText, active && styles.pillTextActive]}>
                    {v}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </View>

        {/* ── Spacer — must NOT intercept touches ── */}
        <View style={{ flex: 1 }} pointerEvents="none" />

        {/* ── Time step selector (bottom) ── */}
        <View style={styles.timeBar} pointerEvents="auto">
          <Text style={styles.timeLabel}>Time Step</Text>
          <View style={styles.timeRow}>
            {TIME_STEPS.map((t) => {
              const active = t === activeTimeStep;
              return (
                <TouchableOpacity
                  key={t}
                  style={[styles.timeChip, active && styles.timeChipActive]}
                  onPress={() => setActiveTimeStep(t)}
                  activeOpacity={0.75}
                >
                  <Text
                    style={[
                      styles.timeChipText,
                      active && styles.timeChipTextActive,
                    ]}
                  >
                    {t}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </View>
      </SafeAreaView>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const GLASS = "rgba(15, 15, 25, 0.72)";
const ACCENT = "#4fc3f7";

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#0a0a14",
  },

  // Transparent overlay that sits on top of the map
  overlay: {
    ...StyleSheet.absoluteFillObject,
    flexDirection: "column",
  },

  // ── Variable pills ──────────────────────────────────────────
  topBar: {
    paddingTop: Platform.OS === "android" ? 44 : 8,
  },
  pillRow: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    gap: 8,
    flexDirection: "row",
  },
  pill: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: GLASS,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.15)",
  },
  pillActive: {
    backgroundColor: ACCENT,
    borderColor: ACCENT,
  },
  pillText: {
    color: "rgba(255,255,255,0.75)",
    fontSize: 13,
    fontWeight: "600",
    letterSpacing: 0.3,
  },
  pillTextActive: {
    color: "#0a0a14",
  },

  // ── Time step row ───────────────────────────────────────────
  timeBar: {
    marginHorizontal: 14,
    marginBottom: 14,
    backgroundColor: GLASS,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.12)",
    paddingVertical: 12,
    paddingHorizontal: 14,
  },
  timeLabel: {
    color: "rgba(255,255,255,0.5)",
    fontSize: 11,
    fontWeight: "600",
    letterSpacing: 0.8,
    textTransform: "uppercase",
    marginBottom: 10,
  },
  timeRow: {
    flexDirection: "row",
    gap: 8,
  },
  timeChip: {
    flex: 1,
    paddingVertical: 9,
    borderRadius: 10,
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.07)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
  },
  timeChipActive: {
    backgroundColor: ACCENT,
    borderColor: ACCENT,
  },
  timeChipText: {
    color: "rgba(255,255,255,0.65)",
    fontSize: 12,
    fontWeight: "600",
  },
  timeChipTextActive: {
    color: "#0a0a14",
  },
});
