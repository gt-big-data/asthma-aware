import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useEffect, useState } from "react";
import { Modal, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { api } from "../lib/api";
import { coordinatePlace, fromSavedPlace, type DashboardPlace, type GeorgiaCity, type SavedLocation } from "../lib/saved-locations";

export default function DashboardLocationPicker({ userId, selected, onSelect, onClose }: {
  userId?: string;
  selected: DashboardPlace | null;
  onSelect: (place: DashboardPlace | null) => Promise<void>;
  onClose: () => void;
}) {
  const router = useRouter();
  const [latitude, setLatitude] = useState(selected?.kind === "coordinates" ? String(selected.latitude) : "");
  const [longitude, setLongitude] = useState(selected?.kind === "coordinates" ? String(selected.longitude) : "");
  const [query, setQuery] = useState("");
  const [cities, setCities] = useState<GeorgiaCity[]>([]);
  const [saved, setSaved] = useState<SavedLocation[]>([]);
  const [draft, setDraft] = useState<GeorgiaCity | null>(null);
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");
  const [searchError, setSearchError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [savedReady, setSavedReady] = useState(!userId);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    if (userId) void api.savedLocations().then(result => {
      if (active) { setSaved(result.items); setSavedReady(true); }
    }).catch(() => { if (active) { setSavedReady(false); setError("Could not load saved places. Try again before saving changes."); } });
    return () => { active = false; };
  }, [userId, retry]);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setLoading(true); setSearchError("");
      try {
        const result = await api.searchCities(query, controller.signal);
        if (active) setCities(result.items);
      } catch { if (active) { setCities([]); setSearchError("City search is unavailable. Try again."); } }
      finally { if (active) setLoading(false); }
    }, 200);
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [query, retry]);

  async function choose(place: DashboardPlace | null) {
    setBusy(true); setError("");
    try { await onSelect(place); onClose(); }
    catch { setError("Could not select this place. Try again."); }
    finally { setBusy(false); }
  }
  async function chooseCoordinates() {
    setBusy(true); setError("");
    try {
      if (!latitude.trim() || !longitude.trim()) throw new Error("Enter both latitude and longitude.");
      const place = coordinatePlace(Number(latitude.trim()), Number(longitude.trim()), false);
      const coverage = await api.locationCoverage(place.latitude, place.longitude);
      if (!coverage.in_georgia) throw new Error("Choose coordinates within Georgia.");
      await onSelect({ ...place, forecast_supported: coverage.forecast_supported }); onClose();
    } catch (error) { setError(error instanceof Error ? error.message : "Could not select these coordinates."); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!draft) return;
    setBusy(true); setError("");
    try {
      const result = await api.saveLocation(draft.id, label.trim() || undefined);
      await onSelect(fromSavedPlace(result)); onClose();
    } catch { setError("Could not save this place. Check your connection and try again."); }
    finally { setBusy(false); }
  }
  async function remove(place: SavedLocation) {
    setBusy(true); setError("");
    try {
      await api.deleteLocation(place.city_id);
      setSaved(items => items.filter(item => item.city_id !== place.city_id));
      if (selected?.id === place.city_id) await onSelect(null);
    } catch { setError("Could not remove this place. Try again."); }
    finally { setBusy(false); }
  }

  return <Modal visible animationType="slide" onRequestClose={() => { if (!busy) onClose(); }}>
    <SafeAreaView style={styles.screen}>
      <View style={styles.sheet}>
        <View style={styles.heading}><View style={styles.flex}><Text style={styles.eyebrow}>YOUR PLACES</Text>
          <Text style={styles.title}>Choose a location</Text></View>
          <TouchableOpacity disabled={busy} onPress={onClose} accessibilityRole="button" accessibilityLabel="Close location picker" style={styles.close}>
            <Ionicons name="close" size={23} color="#52715d" />
          </TouchableOpacity>
        </View>
        <Text style={styles.subtitle}>City or precise coordinates · Georgia only</Text>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.content}>
          <TouchableOpacity disabled={busy} style={styles.current} onPress={() => void choose(null)} accessibilityRole="button" accessibilityLabel="Use current location">
            <Ionicons name="locate-outline" size={20} color="#2d6a4f" /><View style={styles.flex}>
              <Text style={styles.rowTitle}>Current location</Text><Text style={styles.small}>Use your device’s location</Text></View>
            {!selected && <Ionicons name="checkmark-circle" size={19} color="#2d6a4f" />}
          </TouchableOpacity>
          <Text style={styles.sectionTitle}>Choose coordinates</Text>
          <Text style={styles.note}>Enter a Georgia location, or select a 0.01° cell on the forecast map.</Text>
          <View style={styles.actions}>
            <TextInput accessibilityLabel="Dashboard latitude" placeholder="Latitude, e.g. 33.755" value={latitude}
              onChangeText={setLatitude} editable={!busy} keyboardType="numbers-and-punctuation" maxLength={20} style={[styles.labelInput,styles.flex]} />
            <TextInput accessibilityLabel="Dashboard longitude" placeholder="Longitude, e.g. -84.385" value={longitude}
              onChangeText={setLongitude} editable={!busy} keyboardType="numbers-and-punctuation" maxLength={20} style={[styles.labelInput,styles.flex]} />
          </View>
          <View style={styles.actions}>
            <TouchableOpacity disabled={busy} style={styles.viewButton} onPress={() => void chooseCoordinates()} accessibilityRole="button" accessibilityLabel="Use dashboard coordinates">
              <Text style={styles.viewText}>Use coordinates</Text>
            </TouchableOpacity>
            <TouchableOpacity disabled={busy} style={styles.saveButton} onPress={() => { onClose(); router.push({pathname:"/map",params:{pickDashboard:"1"}}); }} accessibilityRole="button" accessibilityLabel="Choose dashboard grid cell on map">
              <Text style={styles.saveText}>Choose on map</Text>
            </TouchableOpacity>
          </View>
          {selected?.kind === "coordinates" && <Text style={styles.small}>Selected: {selected.name}</Text>}
          <Text style={styles.sectionTitle}>Saved locations</Text>
          {!userId && <Text style={styles.note}>Sign in from the Symptom Log tab to save places to your account. You can still explore any Georgia city.</Text>}
          {userId && savedReady && !saved.length && <Text style={styles.note}>Save a city below, with an optional label like Home or Work.</Text>}
          {saved.map(place => <View key={place.city_id} style={styles.savedRow}>
            <TouchableOpacity disabled={busy} style={[styles.savedChoice, selected?.id === place.city_id && styles.active]}
              accessibilityRole="button" accessibilityLabel={`View ${place.label}, ${place.city_name}, Georgia`} onPress={() => void choose(fromSavedPlace(place))}>
              <Ionicons name="bookmark-outline" size={18} color="#52715d" /><View style={styles.flex}>
                <Text style={styles.rowTitle}>{place.label}</Text><Text style={styles.small}>{place.city_name}, GA</Text>
                {!place.forecast_supported && <Text style={styles.coverage}>Outside current forecast coverage</Text>}
              </View>
            </TouchableOpacity>
            <TouchableOpacity disabled={busy} onPress={() => { setDraft(fromSavedPlace(place)); setLabel(place.label); }}
              style={styles.delete} accessibilityRole="button" accessibilityLabel={`Rename saved location ${place.label}`}>
              <Ionicons name="create-outline" size={17} color="#52715d" />
            </TouchableOpacity>
            <TouchableOpacity disabled={busy} onPress={() => void remove(place)} style={styles.delete} accessibilityRole="button" accessibilityLabel={`Remove saved location ${place.label}`}>
              <Ionicons name="trash-outline" size={17} color="#9e6c60" />
            </TouchableOpacity>
          </View>)}
          {!!error && <View style={styles.errorBox}><Text style={styles.error}>{error}</Text>
            <TouchableOpacity disabled={busy} onPress={() => { setError(""); setRetry(value => value + 1); }} accessibilityRole="button"><Text style={styles.link}>Retry</Text></TouchableOpacity>
          </View>}
          <Text style={styles.sectionTitle}>Find a Georgia city</Text>
          <View style={styles.search}><Ionicons name="search-outline" size={18} color="#839187" />
            <TextInput accessibilityLabel="Search Georgia cities" placeholder="Search Atlanta, Athens, Macon…" placeholderTextColor="#9aa79d"
              value={query} maxLength={80} onChangeText={value => { setQuery(value); setDraft(null); setCities([]); setLoading(true); }} style={styles.input} autoCorrect={false} />
          </View>
          {loading ? <Text style={styles.note}>Searching cities…</Text> : searchError ? <TouchableOpacity onPress={() => setRetry(value => value + 1)} accessibilityRole="button">
            <Text style={styles.error}>{searchError}</Text></TouchableOpacity> : !cities.length && <Text style={styles.note}>No Georgia places match that search.</Text>}
          {!loading && cities.map(city => <TouchableOpacity key={city.id} disabled={busy} accessibilityRole="button"
            accessibilityLabel={`Choose ${city.name}, Georgia`} accessibilityState={{ selected: draft?.id === city.id }}
            onPress={() => { setDraft(city); setLabel(saved.find(item => item.city_id === city.id)?.label ?? ""); }}
            style={[styles.city, draft?.id === city.id && styles.active]}>
            <Ionicons name="location-outline" size={17} color="#718276" /><View style={styles.flex}>
              <Text style={styles.rowTitle}>{city.name}, GA</Text>
              {!city.forecast_supported && <Text style={styles.coverage}>Forecast coverage not available yet</Text>}
            </View>{draft?.id === city.id && <Ionicons name="checkmark" size={19} color="#2d6a4f" />}
          </TouchableOpacity>)}
        </ScrollView>
        {draft && <View style={styles.footer}>
          <Text style={styles.rowTitle}>{draft.name}, GA</Text>
          {!draft.forecast_supported && <Text style={styles.coverage}>You can save this city, but its forecast is not available in the current model area.</Text>}
          {userId && <TextInput accessibilityLabel="Saved location label" value={label} onChangeText={setLabel} maxLength={80}
            placeholder="Optional label, e.g. Home" placeholderTextColor="#9aa79d" style={styles.labelInput} />}
          <View style={styles.actions}>
            <TouchableOpacity disabled={busy} style={styles.viewButton} onPress={() => void choose({ ...draft, label: label.trim() || draft.name })} accessibilityRole="button">
              <Text style={styles.viewText}>View forecast</Text>
            </TouchableOpacity>
            {userId && <TouchableOpacity disabled={busy || !savedReady || (saved.length >= 10 && !saved.some(item => item.city_id === draft.id))}
              style={[styles.saveButton, (busy || !savedReady || (saved.length >= 10 && !saved.some(item => item.city_id === draft.id))) && styles.disabled]}
              onPress={() => void save()} accessibilityRole="button"><Text style={styles.saveText}>{busy ? "Saving…" : "Save & view"}</Text></TouchableOpacity>}
          </View>
          {saved.length >= 10 && <Text style={styles.small}>Up to 10 saved locations. Remove a place to add another.</Text>}
        </View>}
      </View>
    </SafeAreaView>
  </Modal>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#f4f7f4" },
  sheet: { flex: 1, width: "100%", maxWidth: 620, alignSelf: "center", padding: 20 },
  heading: { flexDirection: "row", alignItems: "center", gap: 12 },
  flex: { flex: 1 },
  eyebrow: { color: "#52715d", fontSize: 9, letterSpacing: 1.8, fontWeight: "800", marginBottom: 6 },
  title: { fontSize: 25, color: "#253b2d", fontWeight: "700", letterSpacing: -0.6 },
  close: { padding: 9 },
  subtitle: { fontSize: 12, color: "#7d8f82", marginTop: 8, marginBottom: 12 },
  content: { gap: 9, paddingBottom: 24 },
  current: { backgroundColor: "#eaf2ec", flexDirection: "row", alignItems: "center", gap: 11, padding: 15, borderRadius: 15 },
  rowTitle: { color: "#35523f", fontSize: 13, fontWeight: "600" },
  small: { color: "#839187", fontSize: 10, marginTop: 3 },
  sectionTitle: { fontSize: 13, fontWeight: "700", color: "#4f6958", marginTop: 15, marginBottom: 3 },
  note: { color: "#839187", fontSize: 12, lineHeight: 18, paddingVertical: 5 },
  savedRow: { flexDirection: "row", gap: 6 },
  savedChoice: { flex: 1, flexDirection: "row", alignItems: "center", gap: 10, borderRadius: 12, padding: 13, backgroundColor: "#fff" },
  delete: { justifyContent: "center", padding: 12 },
  active: { backgroundColor: "#e4eee5" },
  coverage: { color: "#9a743e", fontSize: 10, lineHeight: 15, marginTop: 4 },
  search: { flexDirection: "row", alignItems: "center", gap: 9, backgroundColor: "#fff", paddingHorizontal: 12, borderRadius: 12, borderWidth: 1, borderColor: "#dce5de" },
  input: { flex: 1, paddingVertical: 13, color: "#35523f", fontSize: 13 },
  city: { flexDirection: "row", alignItems: "center", gap: 9, padding: 12, borderRadius: 11, backgroundColor: "#fff" },
  footer: { paddingTop: 15, borderTopWidth: 1, borderTopColor: "#dce5de", gap: 10 },
  labelInput: { padding: 12, backgroundColor: "#fff", borderRadius: 11, color: "#35523f", fontSize: 13 },
  actions: { flexDirection: "row", gap: 10 },
  viewButton: { flex: 1, padding: 13, borderRadius: 12, alignItems: "center", backgroundColor: "#e4eee5" },
  viewText: { color: "#2d6a4f", fontWeight: "700", fontSize: 12 },
  saveButton: { flex: 1, padding: 13, borderRadius: 12, alignItems: "center", backgroundColor: "#2d6a4f" },
  saveText: { color: "#fff", fontWeight: "700", fontSize: 12 },
  disabled: { opacity: 0.4 },
  errorBox: { backgroundColor: "#f8ece7", padding: 11, borderRadius: 10, gap: 6 },
  error: { color: "#a05a48", fontSize: 12, lineHeight: 17 },
  link: { color: "#2d6a4f", fontWeight: "700", fontSize: 12 },
});
