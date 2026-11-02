import * as Location from "expo-location";
import { useEffect, useRef, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { api } from "../../lib/api";
import { ApiError, type SymptomEntry } from "../../lib/api-client";
import { dateLabel, type Coordinates, type LocationSource } from "../../lib/logging";
import { LocationPicker } from "./LocationPicker";
import { Button, Card, colors, Loading, Notice, styles } from "./ui";

export function SymptomEditor({ day, today, timezone, editable, now, onDirty, onBusy }: {
  day: string; today: string; timezone: string; editable: boolean; now: number;
  onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void;
}) {
  const [entry, setEntry] = useState<SymptomEntry | null>(null);
  const [severity, setSeverity] = useState<number | null>(null);
  const [point, setPoint] = useState<Coordinates | null>(null);
  const [source, setSource] = useState<LocationSource>("current");
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [locating, setLocating] = useState(false);
  const [picker, setPicker] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [reload, setReload] = useState(0);
  const active = useRef(true);
  const dirty = entry ? severity !== entry.severity || point?.latitude !== entry.latitude || point?.longitude !== entry.longitude || source !== entry.location_source
    : severity !== null || point !== null;
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => { onBusy(loading || saving || locating); }, [loading, saving, locating, onBusy]);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let current = true;
    api.entry(day).then((value) => {
      if (!current) return;
      setEntry(value); setSeverity(value?.severity ?? null);
      setPoint(value ? { latitude: value.latitude, longitude: value.longitude } : null);
      setSource(value?.location_source ?? "current");
    }).catch((failure: unknown) => {
      if (current) { setError(failure instanceof Error ? failure.message : "Could not load this entry."); setLoadFailed(true); }
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [day, reload]);

  function reloadEntry() {
    setLoading(true); setLoadFailed(false); setError(null); setConflict(false); setMessage(null);
    setReload((value) => value + 1);
  }

  async function locate() {
    setLocating(true); setError(null); setMessage(null);
    try {
      const permission = await Location.requestForegroundPermissionsAsync();
      if (permission.status !== "granted") throw new Error("Location access was not granted. You can choose a location on the map instead.");
      let timeout: ReturnType<typeof setTimeout> | undefined;
      const location = await Promise.race([
        Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }),
        new Promise<never>((_resolve, reject) => { timeout = setTimeout(() => reject(new Error("Finding your location took too long. You can choose a place on the map instead.")), 15000); }),
      ]).finally(() => clearTimeout(timeout));
      if (!active.current) return;
      setPoint({ latitude: location.coords.latitude, longitude: location.coords.longitude }); setSource("current");
    } catch (failure) {
      if (active.current) setError(failure instanceof Error ? failure.message : "Could not find your location. Please choose one on the map.");
    } finally { if (active.current) setLocating(false); }
  }
  async function save() {
    if (!point || severity === null || saving || !editable) return;
    setSaving(true); setError(null); setMessage(null);
    try {
      const value = await api.save(day, { ...point, severity, location_source: source, expected_revision: entry?.revision ?? 0 });
      if (!active.current) return;
      setEntry(value); setMessage(`Saved for ${dateLabel(day)}.`);
    } catch (failure) {
      if (!active.current) return;
      setError(failure instanceof Error ? failure.message : "Could not save your entry.");
      if (failure instanceof ApiError && failure.status === 409) setConflict(true);
    } finally { if (active.current) setSaving(false); }
  }
  if (loading) return <Card><Loading title="Loading your entry…" /></Card>;
  if (loadFailed) return <Card><Notice error>{error}</Notice><Button title="Try again" onPress={reloadEntry} /></Card>;
  const busy = saving || locating;
  const final = !!entry && (entry.status === "final" || now >= Date.parse(entry.finalizes_at));
  return <>
    <Card>
      <View style={{ gap: 5 }}>
        <Text style={styles.eyebrow}>{entry ? final ? editable ? "FINAL ENTRY · EDITABLE" : "FINAL ENTRY" : "TODAY · PROVISIONAL" : "NEW ENTRY"}</Text>
        <Text style={styles.heading}>{day === today ? "How severe were your symptoms today?" : `How severe were your symptoms on ${dateLabel(day)}?`}</Text>
      </View>
      <View style={[styles.row, { justifyContent: "space-between" }]}>
        {[1, 2, 3, 4, 5].map((value) => <Pressable key={value} accessibilityRole="radio" accessibilityLabel={`Severity ${value} of 5`}
          accessibilityState={{ checked: value === severity, disabled: busy }} disabled={busy} onPress={() => { setSeverity(value); setMessage(null); }}
          style={{ flex: 1, minHeight: 56, borderRadius: 14, borderWidth: 1, alignItems: "center", justifyContent: "center",
            backgroundColor: value === severity ? colors.accent : colors.background, borderColor: value === severity ? colors.accent : colors.border }}>
          <Text style={{ color: value === severity ? colors.background : colors.text, fontSize: 22, fontWeight: "700" }}>{value}</Text>
        </Pressable>)}
      </View>
      <Notice>1 — No symptoms today{"\n"}5 — Very severe; unable to do daily activities</Notice>
    </Card>
    <Card>
      <Text style={styles.heading}>Representative location</Text>
      <Notice>{day === today ? "Use where you are now, or choose a place that better represents your day." : "Choose where you were that day. Your current location may be different."}</Notice>
      {point && <View style={{ gap: 4 }}>
        <Text style={styles.label}>{source === "current" ? "Current location" : "Selected location"}</Text>
        <Notice>{point.latitude.toFixed(4)}, {point.longitude.toFixed(4)}</Notice>
      </View>}
      <Button secondary title={locating ? "Finding your location…" : "Use current location"} disabled={busy} onPress={() => void locate()} />
      <Button secondary title="Choose a different location" disabled={busy} onPress={() => setPicker(true)} />
    </Card>
    {error && <Notice error>{error}</Notice>}
    {message && <Notice>{message}</Notice>}
    {!editable && <Notice error>This date is outside the five-day editing window. Choose a more recent date.</Notice>}
    {conflict ? <Card><Notice>Reloading will replace your unsaved values with the latest saved entry.</Notice>
      <Button title="Reload latest entry" onPress={reloadEntry} /></Card>
      : <Button title={saving ? "Saving…" : entry ? "Save changes" : "Save entry"} disabled={busy || !editable || severity === null || !point || !dirty} onPress={() => void save()} />}
    <Notice>{day === today && !final ? `This entry becomes final at midnight in ${entry?.timezone ?? timezone}.` : `This entry covers a day in ${entry?.timezone ?? timezone}.`} You can edit today and the previous five days. Unsaved changes stay on this screen only.</Notice>
    {picker && <LocationPicker initial={point} onCancel={() => setPicker(false)} onSelect={(value) => {
      setPoint(value); setSource("selected"); setPicker(false); setMessage(null);
    }} />}
  </>;
}
