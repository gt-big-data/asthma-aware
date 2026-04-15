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
import ChoroplethView from "../../components/ChoroplethView";
import {
  SOCIO_VARIABLES,
  SocioVar,
  VARIABLE_META,
  SOCIO_DATA,
  ZipcodeDataPoint,
  initializeSocioData,
} from "../../constants/socioeconomicData";

export default function Socioeconomic() {
  const [activeVar, setActiveVar]     = useState<SocioVar>(SOCIO_VARIABLES[0]);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState<string | null>(null);
  const [dataLoaded, setDataLoaded]   = useState(false);

  // ── Load data on mount ────────────────────────────────────────────────────
  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      setLoading(true);
      setError(null);
      await initializeSocioData();
      setDataLoaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }

  // ── Derived state ─────────────────────────────────────────────────────────
  const currentData: ZipcodeDataPoint[] = dataLoaded
    ? SOCIO_DATA[activeVar] ?? []
    : [];
  const currentMeta = VARIABLE_META[activeVar];

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <View style={styles.root}>
      {/* Map / loading / error */}
      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={ACCENT} />
          <Text style={styles.loadingText}>Loading socioeconomic data…</Text>
        </View>
      ) : error ? (
        <View style={styles.errorContainer}>
          <Text style={styles.errorTitle}>⚠️ Error Loading Data</Text>
          <Text style={styles.errorText}>{error}</Text>
          <Text style={styles.errorHint}>
            Displaying sample data · Backend: http://127.0.0.1:8000
          </Text>
          <TouchableOpacity style={styles.retryButton} onPress={loadData}>
            <Text style={styles.retryButtonText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <ChoroplethView
          data={currentData}
          meta={currentMeta}
          variableLabel={activeVar}
        />
      )}

      {/* Overlay UI — sits above the map */}
      <SafeAreaView style={styles.overlay} pointerEvents="box-none">
        {/* ── Variable pills (top) ────────────────────────────────────────── */}
        <View style={styles.topBar} pointerEvents="auto">
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.pillRow}
          >
            {SOCIO_VARIABLES.map((v) => {
              const active = v === activeVar;
              return (
                <TouchableOpacity
                  key={v}
                  style={[styles.pill, active && styles.pillActive]}
                  onPress={() => setActiveVar(v)}
                  activeOpacity={0.75}
                  disabled={loading || !!error}
                >
                  <Text style={[styles.pillText, active && styles.pillTextActive]}>
                    {v}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </View>

        {/* ── Spacer (must not intercept touches) ────────────────────────── */}
        <View style={{ flex: 1 }} pointerEvents="none" />

        {/* ── Footer description bar ──────────────────────────────────────── */}
        {dataLoaded && (
          <View style={styles.descBar} pointerEvents="none">
            {/* Direction badge */}
            <View style={[
              styles.directionBadge,
              currentMeta.higherIsBetter ? styles.badgeGood : styles.badgeBad,
            ]}>
              <Text style={styles.badgeText}>
                {currentMeta.higherIsBetter ? "↑ Higher is better" : "↓ Lower is better"}
              </Text>
            </View>

            <Text style={styles.descTitle}>{activeVar}</Text>
            <Text style={styles.descText}>{currentMeta.description}</Text>

            <View style={styles.descFooter}>
              <Text style={styles.descUnit}>Unit: {currentMeta.unit}</Text>
              <Text style={styles.descCount}>{currentData.length} ZIP codes</Text>
            </View>
          </View>
        )}
      </SafeAreaView>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const GLASS  = "rgba(15, 15, 25, 0.72)";
const ACCENT = "#4fc3f7";

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#0a0a14",
  },

  // ── Loading ──────────────────────────────────────────────────────────────
  loadingContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#0a0a14",
  },
  loadingText: {
    color: "rgba(255,255,255,0.6)",
    fontSize: 14,
    marginTop: 16,
    fontWeight: "500",
  },

  // ── Error ────────────────────────────────────────────────────────────────
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
    color: "rgba(255,255,255,0.4)",
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
    marginTop: 4,
  },
  retryButtonText: {
    color: "#0a0a14",
    fontWeight: "bold",
    fontSize: 14,
  },

  // ── Overlay ──────────────────────────────────────────────────────────────
  overlay: {
    ...StyleSheet.absoluteFillObject,
    flexDirection: "column",
  },

  // ── Variable pills ────────────────────────────────────────────────────────
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

  // ── Description footer ────────────────────────────────────────────────────
  descBar: {
    marginHorizontal: 14,
    marginBottom: 14,
    backgroundColor: GLASS,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.12)",
    paddingVertical: 12,
    paddingHorizontal: 14,
  },
  directionBadge: {
    alignSelf: "flex-start",
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 10,
    marginBottom: 8,
  },
  badgeGood: {
    backgroundColor: "rgba(26, 152, 80, 0.25)",
    borderWidth: 1,
    borderColor: "rgba(26, 152, 80, 0.5)",
  },
  badgeBad: {
    backgroundColor: "rgba(215, 48, 39, 0.2)",
    borderWidth: 1,
    borderColor: "rgba(215, 48, 39, 0.45)",
  },
  badgeText: {
    color: "rgba(255,255,255,0.7)",
    fontSize: 10,
    fontWeight: "600",
    letterSpacing: 0.3,
  },
  descTitle: {
    color: ACCENT,
    fontSize: 14,
    fontWeight: "700",
    marginBottom: 3,
    letterSpacing: 0.2,
  },
  descText: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 12,
    lineHeight: 17,
    marginBottom: 8,
  },
  descFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  descUnit: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 11,
    fontStyle: "italic",
  },
  descCount: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 11,
    fontWeight: "500",
  },
});