import { Ionicons } from "@expo/vector-icons";
import * as Location from "expo-location";
import { useEffect, useState } from "react";
import {
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

const RISK_SCORE = 64;
const FORECAST_SCORES = [64, 71, 58, 49];
const US_STATE_ABBREVIATIONS: Record<string, string> = {
  alabama: "AL",
  alaska: "AK",
  arizona: "AZ",
  arkansas: "AR",
  california: "CA",
  colorado: "CO",
  connecticut: "CT",
  delaware: "DE",
  florida: "FL",
  georgia: "GA",
  hawaii: "HI",
  idaho: "ID",
  illinois: "IL",
  indiana: "IN",
  iowa: "IA",
  kansas: "KS",
  kentucky: "KY",
  louisiana: "LA",
  maine: "ME",
  maryland: "MD",
  massachusetts: "MA",
  michigan: "MI",
  minnesota: "MN",
  mississippi: "MS",
  missouri: "MO",
  montana: "MT",
  nebraska: "NE",
  nevada: "NV",
  "new hampshire": "NH",
  "new jersey": "NJ",
  "new mexico": "NM",
  "new york": "NY",
  "north carolina": "NC",
  "north dakota": "ND",
  ohio: "OH",
  oklahoma: "OK",
  oregon: "OR",
  pennsylvania: "PA",
  "rhode island": "RI",
  "south carolina": "SC",
  "south dakota": "SD",
  tennessee: "TN",
  texas: "TX",
  utah: "UT",
  vermont: "VT",
  virginia: "VA",
  washington: "WA",
  "west virginia": "WV",
  wisconsin: "WI",
  wyoming: "WY",
  "district of columbia": "DC",
};

const VARIABLE_TRENDS = [
  {
    name: "CO2",
    value: "418",
    unit: "ppm",
    color: "#55c7f3",
    values: [390, 402, 398, 415, 408, 422, 418],
    change: "+7.2%",
  },
  {
    name: "NDVI",
    value: "0.46",
    unit: "index",
    color: "#76d6a1",
    values: [0.37, 0.39, 0.4, 0.43, 0.41, 0.45, 0.46],
    change: "+24.3%",
  },
  {
    name: "SO2",
    value: "3.8",
    unit: "ppb",
    color: "#ffc36e",
    values: [4.4, 4.1, 4.3, 3.9, 4.2, 3.7, 3.8],
    change: "−13.6%",
  },
  {
    name: "Pollen",
    value: "High",
    unit: "level",
    color: "#d7a6ff",
    values: [2, 3, 4, 5, 4, 6, 7],
    change: "+5.0%",
  },
];

function getRiskColor(score: number): string {
  if (score < 40) return "#55d6a0";
  if (score < 70) return "#ffc36e";
  return "#ff7585";
}

function getRiskLabel(score: number): string {
  if (score < 40) return "Low";
  if (score < 70) return "Moderate";
  return "High";
}

function getDayLabels(): string[] {
  return Array.from({ length: 4 }, (_, offset) => {
    const day = new Date();
    day.setDate(day.getDate() + offset);
    return offset === 0
      ? "Today"
      : day.toLocaleDateString("en-US", { weekday: "short" });
  });
}

function getCityAndState(address: Location.LocationGeocodedAddress): string | null {
  const city = address.city?.trim();
  const region = address.region?.trim();
  if (!city || !region) return null;

  const state = /^[A-Za-z]{2}$/.test(region)
    ? region.toUpperCase()
    : US_STATE_ABBREVIATIONS[region.toLowerCase()];
  return state ? `${city}, ${state}` : null;
}

function RiskGauge() {
  const tickCount = 31;
  const centerX = 130;
  const centerY = 124;
  const radius = 106;

  return (
    <View style={styles.gauge} accessibilityLabel={`Risk score ${RISK_SCORE} out of 100, ${getRiskLabel(RISK_SCORE)} risk`}>
      {Array.from({ length: tickCount }, (_, index) => {
        const progress = index / (tickCount - 1);
        const angle = 180 + progress * 180;
        const radians = (angle * Math.PI) / 180;
        const scoreAtTick = progress * 100;
        const color = getRiskColor(scoreAtTick);
        const active = scoreAtTick <= RISK_SCORE;

        return (
          <View
            key={index}
            style={[
              styles.gaugeTick,
              {
                left: centerX + Math.cos(radians) * radius - 2,
                top: centerY + Math.sin(radians) * radius - 7,
                backgroundColor: active ? color : "#29384a",
                transform: [{ rotate: `${angle - 270}deg` }],
              },
            ]}
          />
        );
      })}
      <View style={styles.gaugeReading}>
        <Text style={styles.gaugeScore}>{RISK_SCORE}</Text>
        <Text style={styles.gaugeOutOf}>OUT OF 100</Text>
        <Text style={[styles.gaugeRisk, { color: getRiskColor(RISK_SCORE) }]}>
          {getRiskLabel(RISK_SCORE).toUpperCase()} RISK
        </Text>
      </View>
      <View style={styles.gaugeScale}>
        <Text style={styles.gaugeScaleText}>LOW</Text>
        <Text style={styles.gaugeScaleText}>HIGH</Text>
      </View>
    </View>
  );
}

function Sparkline({
  values,
  color,
}: {
  values: number[];
  color: string;
}) {
  const [width, setWidth] = useState(0);
  const height = 46;
  const min = Math.min(...values);
  const range = Math.max(...values) - min || 1;
  const points = values.map((value, index) => ({
    x: (index / (values.length - 1)) * Math.max(width - 8, 0) + 4,
    y: height - 5 - ((value - min) / range) * (height - 10),
  }));

  return (
    <View
      style={styles.sparkline}
      onLayout={(event) => setWidth(event.nativeEvent.layout.width)}
      accessible={false}
    >
      {points.slice(1).map((point, index) => {
        const previous = points[index];
        const dx = point.x - previous.x;
        const dy = point.y - previous.y;
        const length = Math.sqrt(dx * dx + dy * dy);
        const angle = Math.atan2(dy, dx);

        return (
          <View
            key={index}
            style={[
              styles.sparklineSegment,
              {
                left: (point.x + previous.x - length) / 2,
                top: (point.y + previous.y - 2) / 2,
                width: length,
                backgroundColor: color,
                transform: [{ rotate: `${angle}rad` }],
              },
            ]}
          />
        );
      })}
      {points.length > 0 && (
        <View
          style={[
            styles.sparklineDot,
            {
              left: points[points.length - 1].x - 3,
              top: points[points.length - 1].y - 3,
              backgroundColor: color,
            },
          ]}
        />
      )}
    </View>
  );
}

export default function DashboardScreen() {
  const [expandedDay, setExpandedDay] = useState<number | null>(null);
  const [cityAndState, setCityAndState] = useState("Locating...");
  const dayLabels = getDayLabels();

  useEffect(() => {
    let isMounted = true;

    async function loadLocation() {
      try {
        const permission = await Location.requestForegroundPermissionsAsync();
        if (permission.status !== "granted") {
          if (isMounted) setCityAndState("Location unavailable");
          return;
        }

        const position = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        const [address] = await Location.reverseGeocodeAsync(position.coords);
        const result = address ? getCityAndState(address) : null;

        if (isMounted) {
          setCityAndState(result ?? "Location unavailable");
        }
      } catch (error) {
        console.error("Unable to retrieve the user's location.", error);
        if (isMounted) setCityAndState("Location unavailable");
      }
    }

    void loadLocation();
    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.header}>
        <View>
          <Text style={styles.eyebrow}>ASTHMA AWARE</Text>
          <Text style={styles.title}>Your dashboard</Text>
        </View>
        <View style={styles.locationPill}>
          <Ionicons name="location-outline" size={15} color="#9aacc1" />
          <Text style={styles.locationText}>{cityAndState}</Text>
        </View>
      </View>

      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <View>
            <Text style={styles.cardTitle}>Today&apos;s Respiratory Risk</Text>
          </View>
        </View>
        <RiskGauge />
      </View>

      <View style={styles.sectionHeading}>
        <View>
          <Text style={styles.sectionTitle}>4-day forecast</Text>
          <Text style={styles.sectionSubtitle}>Predicted respiratory risk</Text>
        </View>
        <Ionicons name="calendar-outline" size={19} color="#8192a7" />
      </View>
      <View style={styles.card}>
        <View style={styles.forecastRow}>
          {dayLabels.map((day, index) => {
            const score = FORECAST_SCORES[index];
            const selected = expandedDay === index;

            return (
              <TouchableOpacity
                key={`${day}-${index}`}
                style={[styles.forecastDay, selected && styles.forecastDaySelected]}
                onPress={() => setExpandedDay(selected ? null : index)}
                activeOpacity={0.75}
                accessibilityRole="button"
                accessibilityLabel={`${day}, risk score ${score}`}
                accessibilityState={{ expanded: selected }}
              >
                <Text style={styles.forecastDayLabel}>{day}</Text>
                <View
                  style={[
                    styles.forecastDot,
                    { backgroundColor: getRiskColor(score) },
                  ]}
                />
                <Text style={styles.forecastScore}>{score}</Text>
              </TouchableOpacity>
            );
          })}
        </View>
        {expandedDay !== null && (
          <View style={styles.forecastDetails}>
            <Text style={styles.forecastDetailsTitle}>
              {dayLabels[expandedDay]} · {getRiskLabel(FORECAST_SCORES[expandedDay])} risk
            </Text>
            <Text style={styles.forecastDetailsText}>
              More forecast details coming soon.
            </Text>
          </View>
        )}
      </View>

      <View style={styles.sectionHeading}>
        <View>
          <Text style={styles.sectionTitle}>Environmental trends</Text>
          <Text style={styles.sectionSubtitle}>Past 7 days</Text>
        </View>
        <Ionicons name="analytics-outline" size={20} color="#8192a7" />
      </View>
      <View style={styles.trendGrid}>
        {VARIABLE_TRENDS.map((trend) => (
          <View key={trend.name} style={styles.trendCard}>
            <View style={styles.trendTop}>
              <View>
                <Text style={styles.trendName}>{trend.name}</Text>
                <View style={styles.trendValueRow}>
                  <Text style={styles.trendValue}>{trend.value}</Text>
                  <Text style={styles.trendUnit}>{trend.unit}</Text>
                </View>
              </View>
              <View
                style={[
                  styles.trendIcon,
                  { backgroundColor: `${trend.color}1A` },
                ]}
              >
                <Ionicons
                  name="trending-up-outline"
                  size={16}
                  color={trend.color}
                />
              </View>
            </View>
            <Sparkline values={trend.values} color={trend.color} />
            <Text style={[styles.trendChange, { color: trend.color }]}>
              {trend.change} this week
            </Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: "#0b1220",
  },
  content: {
    paddingHorizontal: 18,
    paddingTop: 22,
    paddingBottom: 32,
    gap: 14,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 4,
  },
  eyebrow: {
    color: "#55c7f3",
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 2,
    marginBottom: 5,
  },
  title: {
    color: "#f4f7fb",
    fontSize: 25,
    fontWeight: "700",
    letterSpacing: -0.5,
  },
  locationPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#29364a",
    backgroundColor: "#121c2c",
  },
  locationText: {
    color: "#b3c0d0",
    fontSize: 12,
    fontWeight: "600",
  },
  card: {
    alignItems:'center',
    backgroundColor: "#121c2c",
    borderColor: "#25344a",
    borderWidth: 1,
    borderRadius: 20,
    padding: 16,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
  },
  cardTitle: {
    textAlign: 'center',
    color: "#edf3fa",
    fontSize: 16,
    fontWeight: "700",
  },
  cardSubtitle: {
    color: "#8192a7",
    fontSize: 12,
    marginTop: 4,
  },
  livePill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 5,
    backgroundColor: "#263143",
  },
  liveDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: "#ffc36e",
  },
  liveText: {
    color: "#ffc36e",
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 0.7,
  },
  gauge: {
    alignSelf: "center",
    width: 260,
    height: 150,
    marginTop: 2,
  },
  gaugeTick: {
    position: "absolute",
    width: 4,
    height: 14,
    borderRadius: 3,
  },
  gaugeReading: {
    position: "absolute",
    top: 86,
    left: 0,
    right: 0,
    alignItems: "center",
  },
  gaugeScore: {
    color: "#f4f7fb",
    fontSize: 37,
    lineHeight: 42,
    fontWeight: "800",
    letterSpacing: -1.5,
  },
  gaugeOutOf: {
    color: "#8192a7",
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  gaugeRisk: {
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 1,
    marginTop: 4,
  },
  gaugeScale: {
    position: "absolute",
    bottom: 0,
    left: 8,
    right: 8,
    flexDirection: "row",
    justifyContent: "space-between",
  },
  gaugeScaleText: {
    color: "#8192a7",
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 1,
  },
  gaugeFootnote: {
    color: "#73849a",
    fontSize: 11,
    textAlign: "center",
    marginTop: 6,
  },
  sectionHeading: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 7,
    marginBottom: -4,
  },
  sectionTitle: {
    color: "#edf3fa",
    fontSize: 17,
    fontWeight: "700",
  },
  sectionSubtitle: {
    color: "#8192a7",
    fontSize: 12,
    marginTop: 3,
  },
  forecastRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 3,
  },
  forecastDay: {
    alignItems: "center",
    flex: 1,
    paddingVertical: 9,
    borderRadius: 12,
  },
  forecastDaySelected: {
    backgroundColor: "#202e41",
  },
  forecastDayLabel: {
    color: "#9aacc1",
    fontSize: 10,
    fontWeight: "600",
  },
  forecastDot: {
    width: 9,
    height: 9,
    borderRadius: 5,
    marginTop: 12,
    marginBottom: 8,
  },
  forecastScore: {
    color: "#edf3fa",
    fontSize: 13,
    fontWeight: "700",
  },
  forecastDetails: {
    borderTopWidth: 1,
    borderTopColor: "#29364a",
    paddingTop: 12,
    marginTop: 10,
  },
  forecastDetailsTitle: {
    color: "#edf3fa",
    fontSize: 13,
    fontWeight: "700",
  },
  forecastDetailsText: {
    color: "#8192a7",
    fontSize: 12,
    marginTop: 5,
  },
  sampleNote: {
    color: "#73849a",
    fontSize: 10,
    textAlign: "center",
    marginTop: 8,
  },
  trendGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  trendCard: {
    flexGrow: 1,
    flexBasis: "43%",
    minWidth: 130,
    backgroundColor: "#121c2c",
    borderColor: "#25344a",
    borderWidth: 1,
    borderRadius: 18,
    padding: 13,
  },
  trendTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
  },
  trendName: {
    color: "#9aacc1",
    fontSize: 12,
    fontWeight: "600",
  },
  trendValueRow: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: 4,
    marginTop: 4,
  },
  trendValue: {
    color: "#f4f7fb",
    fontSize: 21,
    fontWeight: "700",
  },
  trendUnit: {
    color: "#8192a7",
    fontSize: 10,
  },
  trendIcon: {
    width: 28,
    height: 28,
    borderRadius: 9,
    alignItems: "center",
    justifyContent: "center",
  },
  sparkline: {
    height: 46,
    marginTop: 7,
    overflow: "hidden",
  },
  sparklineSegment: {
    position: "absolute",
    height: 2.5,
    borderRadius: 2,
  },
  sparklineDot: {
    position: "absolute",
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  trendChange: {
    fontSize: 10,
    fontWeight: "700",
    marginTop: 5,
  },
  footerNote: {
    color: "#73849a",
    fontSize: 10,
    lineHeight: 15,
    textAlign: "center",
    marginTop: 2,
  },
});
