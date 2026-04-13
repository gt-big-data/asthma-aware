import { useEffect, useState } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  SafeAreaView,
  Platform,
  ActivityIndicator,
} from "react-native";
import HeatmapView from "../components/HeatmapView";
import {
  VARIABLES,
  TIME_STEPS,
  Variable,
  TimeStep,
  LatLngIntensity,
  initializeHeatmapData,
  HEATMAP_DATA,
} from "../constants/data";

export default function Index() {
  const [activeVariable, setActiveVariable] = useState<Variable>(VARIABLES[0]);
  const [activeTimeStep, setActiveTimeStep] = useState<TimeStep>(TIME_STEPS[0]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dataLoaded, setDataLoaded] = useState(false);

  // Initialize heatmap data from backend on component mount
  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        setError(null);
        await initializeHeatmapData();
        setDataLoaded(true);
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : "Failed to load heatmap data";
        setError(errorMessage);
        console.error("Heatmap data loading error:", err);
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, []);

  // Get current data points based on selected variable and time step
  const points: LatLngIntensity[] = dataLoaded
    ? HEATMAP_DATA[activeVariable][activeTimeStep] || []
    : [];

  return (
    <View style={styles.root}>
      {/* ── Full-screen map ── */}
      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#4fc3f7" />
          <Text style={styles.loadingText}>Loading environmental data...</Text>
        </View>
      ) : error ? (
        <View style={styles.errorContainer}>
          <Text style={styles.errorTitle}>⚠️ Error Loading Data</Text>
          <Text style={styles.errorText}>{error}</Text>
          <Text style={styles.errorHint}>
            Make sure the backend is running at http://127.0.0.1:8000
          </Text>
          <TouchableOpacity
            style={styles.retryButton}
            onPress={() => {
              // Reload the entire app to retry
              console.log("Retry loading data");
              setLoading(true);
              initializeHeatmapData()
                .then(() => {
                  setError(null);
                  setDataLoaded(true);
                })
                .catch((err) => {
                  setError(
                    err instanceof Error
                      ? err.message
                      : "Failed to load data"
                  );
                })
                .finally(() => setLoading(false));
            }}
          >
            <Text style={styles.retryButtonText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <HeatmapView points={points} />
      )}

      <SafeAreaView style={styles.overlay} pointerEvents="box-none">
        {/* ── Variable selector (top) ── */}
        <View style={styles.topBar} pointerEvents="box-none">
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
                  disabled={loading || error !== null}
                >
                  <Text
                    style={[
                      styles.pillText,
                      active && styles.pillTextActive,
                    ]}
                  >
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
                  disabled={loading || error !== null}
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

        {/* ── Data status indicator ── */}
        {dataLoaded && (
          <View style={styles.statusIndicator} pointerEvents="none">
            <View style={styles.statusDot} />
            <Text style={styles.statusText}>
              {points.length} data points loaded
            </Text>
          </View>
        )}
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

  // ── Loading state ───────────────────────────────────────────
  loadingContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#0a0a14",
  },
  loadingText: {
    color: "rgba(255,255,255,0.7)",
    fontSize: 14,
    marginTop: 16,
    fontWeight: "500",
  },

  // ── Error state ─────────────────────────────────────────────
  errorContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#0a0a14",
    paddingHorizontal: 24,
  },
  errorTitle: {
    color: "#ff6b6b",
    fontSize: 18,
    fontWeight: "bold",
    marginBottom: 12,
  },
  errorText: {
    color: "rgba(255,255,255,0.7)",
    fontSize: 14,
    textAlign: "center",
    marginBottom: 8,
  },
  errorHint: {
    color: "rgba(255,255,255,0.5)",
    fontSize: 12,
    textAlign: "center",
    marginBottom: 20,
    fontStyle: "italic",
  },
  retryButton: {
    backgroundColor: ACCENT,
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
    marginTop: 12,
  },
  retryButtonText: {
    color: "#0a0a14",
    fontWeight: "bold",
    fontSize: 14,
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

  // ── Status indicator ────────────────────────────────────────
  statusIndicator: {
    position: "absolute",
    bottom: 120,
    right: 14,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: GLASS,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.12)",
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: ACCENT,
    marginRight: 6,
  },
  statusText: {
    color: "rgba(255,255,255,0.6)",
    fontSize: 11,
    fontWeight: "500",
  },
});