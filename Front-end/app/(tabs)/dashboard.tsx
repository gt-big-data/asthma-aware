import LoadingFeedback, { LoadingSkeleton } from "../../components/LoadingFeedback";
import EnvironmentalTrendsSection from "../../components/EnvironmentalTrendsSection";
import type { EnvironmentalTrends } from "../../lib/environmental-trends";
import { placeLabel } from "../../lib/saved-locations";
import { Ionicons } from "@expo/vector-icons";
import * as Location from "expo-location";
import { useFocusEffect } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { getUserLocation } from "../../constants/userLocation";

import { api } from "../../lib/api";
import { useAuth } from "../../providers/AuthProvider";
import type { DashboardRisk } from "../../lib/api-client";
import HourlyRiskTimeline from "../../components/HourlyRiskTimeline";
import { riskColor as getRiskColor, riskLabel as getRiskLabel } from "../../lib/hourly-risk";
import DashboardLocationPicker from "../../components/DashboardLocationPicker";
import { useDashboardPlace } from "../../lib/use-dashboard-place";
import LowerRiskPeriods from "../../components/LowerRiskPeriods";
import ForecastDetailsPanel from "../../components/ForecastDetailsPanel";
import { useDashboardClock } from "../../lib/use-dashboard-clock";

const ATLANTA = { latitude: 33.75, longitude: -84.39 };
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

function RiskGauge({ score, status }: { score: number | null; status: string }) {
  const tickCount = 31;
  const centerX = 130;
  const centerY = 124;
  const radius = 106;

  return (
    <View style={styles.gauge} accessibilityLabel={score === null ? status : `Risk score ${score} out of 100, ${getRiskLabel(score)} risk`}>
      {Array.from({ length: tickCount }, (_, index) => {
        const progress = index / (tickCount - 1);
        const angle = 180 + progress * 180;
        const radians = (angle * Math.PI) / 180;
        const scoreAtTick = progress * 100;
        const color = getRiskColor(scoreAtTick);
        const active = score !== null && scoreAtTick <= score;

        return (
          <View
            key={index}
            style={[
              styles.gaugeTick,
              {
                left: centerX + Math.cos(radians) * radius - 2,
                top: centerY + Math.sin(radians) * radius - 7,
                backgroundColor: active ? color : "#e1e8e3",
                transform: [{ rotate: `${angle - 270}deg` }],
              },
            ]}
          />
        );
      })}
      <View style={styles.gaugeReading}>
        {score===null&&status==="LOADING"?<View style={{height:42,justifyContent:"center"}}><LoadingFeedback label="Loading current risk forecast" compact/></View>
          :<Text style={styles.gaugeScore}>{score ?? "–"}</Text>}
        <Text style={styles.gaugeOutOf}>OUT OF 100</Text>
        <Text style={[styles.gaugeRisk, { color: score === null ? "#748178" : getRiskColor(score) }]}>
          {score === null ? status : `${getRiskLabel(score).toUpperCase()} RISK`}
        </Text>
      </View>
      <View style={styles.gaugeScale}>
        <Text style={styles.gaugeScaleText}>LOW</Text>
        <Text style={styles.gaugeScaleText}>HIGH</Text>
      </View>
    </View>
  );
}

export default function DashboardScreen() {
  const now = useDashboardClock();
  const { session, loading: authLoading } = useAuth();
  const userId = session?.user.id;
  const { place, ready: placeReady, error: placeError, select: selectPlace } = useDashboardPlace(userId);
  const [showLocationPicker, setShowLocationPicker] = useState(false);
  const [forecastNotice, setForecastNotice] = useState("");
  const [expandedDay, setExpandedDay] = useState<number | null>(null);
  const [cityAndState, setCityAndState] = useState("Locating...");
  const [trends,setTrends] = useState<{key:string;data:EnvironmentalTrends|null;loading:boolean}>({key:"",data:null,loading:true});
  const [loadedForecast, setForecast] = useState<DashboardRisk | null>(null);
  const [loadedContext, setLoadedContext] = useState("");
  const [forecastStatus, setForecastStatus] = useState("LOADING");
  const [reload, setReload] = useState(0);
  const [focused, setFocused] = useState(false);
  const contextKey = `${userId ?? "guest"}/${place?.id ?? "current"}`;
  const contextReady = placeReady && !authLoading && loadedContext === contextKey;
  const forecast = contextReady ? loadedForecast : null;
  const displayStatus = contextReady ? forecastStatus : "LOADING";
  const locationLabel = !placeReady || authLoading ? "Loading location…" : place
    ? placeLabel(place)
    : contextReady ? cityAndState : "Current location";
  const dayLabels = forecast?.days.map(({ date }, index) => index === 0 ? "Today"
    : new Date(`${date}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" }))
    ?? getDayLabels();
  const scores = forecast?.days.map(({ score }) => score) ?? [null, null, null, null];

  useFocusEffect(useCallback(() => {
    setFocused(true);
    return () => setFocused(false);
  }, []));

  useEffect(() => {
    if (!focused || !placeReady || authLoading) return;
    let active = true;
    const controller = new AbortController();
    let locationTimeout: ReturnType<typeof setTimeout>;
    async function loadForecast() {
      setForecast(null);
      setTrends({key:contextKey,data:null,loading:true});
      setLoadedContext(contextKey);
      setForecastStatus("LOADING");
      setForecastNotice("");
      setExpandedDay(null);
      const coords = place ?? await Promise.race([
        getUserLocation().catch(() => null),
        new Promise<null>((resolve) => { locationTimeout = setTimeout(() => resolve(null), 8000); }),
      ]);
      clearTimeout(locationTimeout);
      if (!active) return;

      const { latitude, longitude } = coords ?? ATLANTA;
      void api.environmentalTrends(latitude,longitude,controller.signal).then(data=>{
        if(active) setTrends({key:contextKey,data,loading:false});
      }).catch(()=>{if(active) setTrends({key:contextKey,data:null,loading:false});});
      setCityAndState(place ? placeLabel(place)
        : coords ? "Current location" : "Atlanta, GA");
      if (coords && !place && Platform.OS !== "web") {
        void Location.reverseGeocodeAsync(coords).then(([address]) => {
          const label = address && getCityAndState(address);
          if (active && label) setCityAndState(label);
        }).catch(() => { /* Keep the current-location label. */ });
      }
      try {
        const coverage = place ? { in_georgia: true, forecast_supported: place.forecast_supported }
          : await api.locationCoverage(latitude, longitude, controller.signal);
        if (!active) return;
        if (!coverage.in_georgia || !coverage.forecast_supported) {
          setForecastStatus("UNAVAILABLE");
          setForecastNotice(!coverage.in_georgia ? "Your current location is outside Georgia. Choose a Georgia location to view its dashboard."
            : "This Georgia location is outside the current forecast area. Choose another location to view available forecasts.");
          return;
        }
        const result = await api.dashboardRisk(latitude, longitude, !!userId, controller.signal);
        if (active) { setForecast(result); setForecastStatus("UNAVAILABLE"); }
        if (active && !coords) setForecastNotice("Device location is unavailable. Showing Atlanta, GA. Choose a location to change the location.");
      } catch {
        if (active) { setForecastStatus("UNAVAILABLE"); setForecastNotice("Could not load this location’s forecast. Try again or choose another location."); }
      }
    }
    void loadForecast();
    const refresh = setInterval(() => setReload(value => value + 1), 5 * 60 * 1000);
    return () => { active = false; controller.abort(); clearTimeout(locationTimeout); clearInterval(refresh); };
  }, [userId, reload, focused, place, placeReady, authLoading, contextKey]);

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
        <TouchableOpacity disabled={!placeReady || authLoading} onPress={() => setShowLocationPicker(true)} style={styles.locationPill}
          accessibilityRole="button" accessibilityLabel={`Change dashboard location. ${locationLabel}`}>
          <Ionicons name="location-outline" size={15} color="#66766b" />
          <Text style={styles.locationText} numberOfLines={1}>{locationLabel}</Text>
          <Ionicons name="chevron-down" size={12} color="#66766b" />
        </TouchableOpacity>
      </View>
      {!!((contextReady && forecastNotice) || placeError) && <Text style={styles.locationNotice}>{(contextReady && forecastNotice) || placeError}</Text>}
      {showLocationPicker && <DashboardLocationPicker key={userId ?? "guest"} userId={userId} selected={place}
        onSelect={async next => { await selectPlace(next); setReload(value => value + 1); }} onClose={() => setShowLocationPicker(false)} />}

      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <View>
            <Text style={styles.cardTitle}>Current respiratory risk</Text>
          </View>
        </View>
        <RiskGauge score={forecast?.hourly?.[0]?.score ?? null} status={displayStatus} />
        <Text style={styles.gaugeFootnote}>{displayStatus === "LOADING" ? "Loading today’s outlook…" : scores[0] === null ? "Today’s peak unavailable"
          : `Today’s forecast peak ${scores[0]} / 100`}</Text>
      </View>

      <HourlyRiskTimeline key={`${forecast?.source_run ?? "loading"}/${userId ?? "general"}`} forecast={forecast}
        loading={displayStatus === "LOADING"} onRetry={() => setReload(value => value + 1)} />

      <LowerRiskPeriods forecast={forecast} loading={displayStatus === "LOADING"} now={now} />

      <View style={styles.sectionHeading}>
        <View>
          <Text style={styles.sectionTitle}>4-day overview</Text>
          <Text style={styles.sectionSubtitle}>
            {forecast?.personalization?.matched_logs ? "Personalized respiratory risk" : "General respiratory risk"}
            {forecast?.placeholder ? " · Development estimate" : ""}
            {forecast?.stale ? " · Older NASA run" : ""}
            {forecast?.personalization?.still_learning && forecast.personalization.matched_logs > 0 ? " · Still learning" : ""}
          </Text>
        </View>
        <Ionicons name="calendar-outline" size={19} color="#66766b" />
      </View>
      <View style={styles.card}>
        <View style={styles.forecastRow}>
          {dayLabels.map((day, index) => {
            const score = scores[index];
            const selected = expandedDay === index;

            return (
              <TouchableOpacity
                key={`${day}-${index}`}
                style={[styles.forecastDay, selected && styles.forecastDaySelected]}
                onPress={() => setExpandedDay(selected ? null : index)}
                activeOpacity={0.75}
                accessibilityRole="button"
                disabled={displayStatus === "LOADING"}
                accessibilityLabel={displayStatus === "LOADING" ? `Loading ${day} forecast` : score === null ? `${day}, forecast unavailable` : `${day}, risk score ${score}`}
                accessibilityState={{ expanded: selected, busy: displayStatus === "LOADING" }}
              >
                <Text style={styles.forecastDayLabel}>{day}</Text>
                <View
                  style={[
                    styles.forecastDot,
                    { backgroundColor: score === null ? "#e1e8e3" : getRiskColor(score) },
                  ]}
                />
                {displayStatus==="LOADING"?<View accessibilityRole="progressbar" accessibilityLabel={`Loading ${day} forecast`} accessibilityState={{busy:true}}><LoadingSkeleton width={32} height={16}/></View>
                  :<Text style={styles.forecastScore}>{score ?? "–"}</Text>}
              </TouchableOpacity>
            );
          })}
        </View>
        {expandedDay !== null && (
          <View style={styles.forecastDetails}>
            <Text style={styles.forecastDetailsTitle}>
              {dayLabels[expandedDay]} · {scores[expandedDay] === null
                ? "Unavailable" : `${getRiskLabel(scores[expandedDay])} risk`}
            </Text>
            <Text style={styles.forecastDetailsText}>
              {scores[expandedDay] === null ? "No usable NASA forecast for this day."
                : `Peak score from ${forecast?.days[expandedDay].available_hours} available forecast hours.`}
            </Text>
          </View>
        )}
      </View>

      <EnvironmentalTrendsSection data={placeReady&&!authLoading&&trends.key===contextKey?trends.data:null}
        loading={!placeReady||authLoading||trends.key!==contextKey||trends.loading} onRetry={()=>setReload(value=>value+1)} />
      <ForecastDetailsPanel forecast={forecast} now={now} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: "#f4f7f4",
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
    color: "#2d6a4f",
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 2,
    marginBottom: 5,
  },
  title: {
    color: "#1f2d25",
    fontSize: 25,
    fontWeight: "700",
    letterSpacing: -0.5,
  },
  locationPill: {
    maxWidth: "48%",
    flexShrink: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#d8e3db",
    backgroundColor: "#eaf2ec",
  },
  locationText: {
    flexShrink: 1,
    color: "#4b5b50",
    fontSize: 12,
    fontWeight: "600",
  },
  locationNotice: { color: "#826b49", backgroundColor: "#f6f0e6", borderRadius: 12, padding: 12, fontSize: 12, lineHeight: 18 },
  card: {
    alignItems:'center',
    backgroundColor: "#ffffff",
    borderColor: "#dce5de",
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
    color: "#25352c",
    fontSize: 16,
    fontWeight: "700",
  },
  cardSubtitle: {
    color: "#66766b",
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
    backgroundColor: "#f8edd9",
  },
  liveDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: "#9a650f",
  },
  liveText: {
    color: "#80540d",
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
    color: "#1f2d25",
    fontSize: 37,
    lineHeight: 42,
    fontWeight: "800",
    letterSpacing: -1.5,
  },
  gaugeOutOf: {
    color: "#66766b",
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
    color: "#66766b",
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 1,
  },
  gaugeFootnote: {
    color: "#66766b",
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
    color: "#25352c",
    fontSize: 17,
    fontWeight: "700",
  },
  sectionSubtitle: {
    color: "#66766b",
    fontSize: 12,
    marginTop: 3,
  },
  forecastRow: {
    width: "100%",
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
    backgroundColor: "#eaf2ec",
  },
  forecastDayLabel: {
    color: "#66766b",
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
    color: "#25352c",
    fontSize: 13,
    fontWeight: "700",
  },
  forecastDetails: {
    width: "100%",
    borderTopWidth: 1,
    borderTopColor: "#dce5de",
    paddingTop: 12,
    marginTop: 10,
  },
  forecastDetailsTitle: {
    color: "#25352c",
    fontSize: 13,
    fontWeight: "700",
  },
  forecastDetailsText: {
    color: "#66766b",
    fontSize: 12,
    marginTop: 5,
  },
  sampleNote: {
    color: "#66766b",
    fontSize: 10,
    textAlign: "center",
    marginTop: 8,
  },
  footerNote: {
    color: "#66766b",
    fontSize: 10,
    lineHeight: 15,
    textAlign: "center",
    marginTop: 2,
  },
});
