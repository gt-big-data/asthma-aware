import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, KeyboardAvoidingView, Platform, Pressable, ScrollView, Text, View } from "react-native";
import { useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { AuthForm } from "../../components/logging/AuthForm";
import { SymptomEditor } from "../../components/logging/SymptomEditor";
import { Button, Card, colors, Loading, Notice, styles } from "../../components/logging/ui";
import { api } from "../../lib/api";
import type { Profile } from "../../lib/api-client";
import { availableDates, dateLabel } from "../../lib/logging";
import { supabase } from "../../lib/supabase";
import { useAuth } from "../../providers/AuthProvider";

function Logbook({ email }: { email: string | undefined }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [now, setNow] = useState(new Date());
  const [selected, setSelected] = useState<string | null>(null);
  const [pendingDate, setPendingDate] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [accountBusy, setAccountBusy] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  useEffect(() => {
    let active = true;
    api.profile().then((value) => {
      if (active) {
        const initialDay = availableDates(value.timezone)[0];
        setProfile(value); setSelected((previous) => previous ?? initialDay); setError(null);
      }
    })
      .catch((failure: unknown) => { if (active) setError(failure instanceof Error ? failure.message : "Could not load your account."); });
    return () => { active = false; };
  }, [retry]);
  useEffect(() => {
    const tick = setInterval(() => setNow(new Date()), 30000);
    const listener = AppState.addEventListener("change", (state) => { if (state === "active") setNow(new Date()); });
    return () => { clearInterval(tick); listener.remove(); };
  }, []);
  useEffect(() => { dirtyRef.current = dirty; }, [dirty]);
  useFocusEffect(useCallback(() => {
    setNow(new Date());
    // Retain unsaved values when the user briefly switches tabs.
    if (!dirtyRef.current) setRefreshVersion((value) => value + 1);
  }, []));
  const dates = profile ? availableDates(profile.timezone, now) : [];
  const day = selected ?? dates[0];
  const deviceTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  async function setTimezone() {
    setAccountBusy(true); setError(null);
    try {
      const value = await api.setTimezone(deviceTimezone);
      setProfile(value); setSelected(availableDates(value.timezone)[0]); setRefreshVersion((value) => value + 1);
    }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Could not change your timezone."); }
    finally { setAccountBusy(false); }
  }
  async function signOut() {
    if (dirty) { setError("Save your entry or discard your changes before signing out."); return; }
    setAccountBusy(true); setError(null);
    try { const result = await supabase?.auth.signOut({ scope: "local" }); if (result?.error) setError(result.error.message); }
    catch { setError("Could not sign out. Please try again."); }
    finally { setAccountBusy(false); }
  }
  return <>
    <View style={{ gap: 8 }}>
      <Text style={styles.note}>{email}</Text>
      <View style={styles.row}>
        <View style={{ flex: 1 }}><Notice>Dates use {profile?.timezone ?? "your account timezone"}.</Notice></View>
        <Button secondary title="Sign out" disabled={busy || accountBusy} onPress={() => void signOut()} />
      </View>
    </View>
    {error && <Notice error>{error}</Notice>}
    {!profile ? error ? <Button title="Try again" onPress={() => setRetry((value) => value + 1)} /> : <Loading title="Loading your account…" /> : <>
      {deviceTimezone && deviceTimezone !== profile.timezone && <Card>
        <Notice>Your device uses {deviceTimezone}. Changing the account timezone affects new entries only.</Notice>
        <Button secondary title={`Use ${deviceTimezone}`} disabled={dirty || busy || accountBusy} onPress={() => void setTimezone()} />
      </Card>}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
        {dates.map((value, index) => <Pressable key={value} accessibilityRole="button" accessibilityLabel={`Log for ${index === 0 ? "today" : dateLabel(value)}`}
          accessibilityState={{ selected: day === value, disabled: busy || accountBusy }} disabled={busy || accountBusy}
          onPress={() => { if (value === day) return; if (dirty) setPendingDate(value); else { setSelected(value); setDirty(false); } }}
          style={{ padding: 12, minWidth: 76, borderRadius: 12, backgroundColor: day === value ? colors.accent : colors.card, borderWidth: 1, borderColor: colors.border }}>
          <Text style={{ color: day === value ? colors.background : colors.text, textAlign: "center", fontWeight: "600" }}>{index === 0 ? "Today" : index === 1 ? "Yesterday" : dateLabel(value)}</Text>
        </Pressable>)}
      </ScrollView>
      {pendingDate && <Card><Notice>Switch dates and discard your unsaved changes?</Notice>
        <Button title="Discard changes and switch" onPress={() => { setDirty(false); setSelected(pendingDate); setPendingDate(null); }} />
        <Button secondary title="Keep editing" onPress={() => setPendingDate(null)} /></Card>}
      <SymptomEditor key={`${day}-${refreshVersion}`} day={day} today={dates[0]} timezone={profile.timezone} editable={dates.includes(day)} now={now.getTime()} onDirty={setDirty} onBusy={setBusy} />
      {dirty && <Button secondary title="Discard unsaved changes" disabled={busy} onPress={() => { setDirty(false); setRefreshVersion((value) => value + 1); }} />}
    </>}
  </>;
}

export default function SymptomsScreen() {
  const { session, loading, error } = useAuth();
  const insets = useSafeAreaInsets();
  return <KeyboardAvoidingView style={styles.screen} behavior={Platform.OS === "ios" ? "padding" : undefined}>
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={[styles.content, { paddingTop: 24 + insets.top }]}>
      <View style={{ gap: 6 }}><Text style={styles.eyebrow}>ASTHMAAWARE</Text><Text style={styles.title}>Your symptom log</Text>
        <Notice>A quick daily check-in, with room to update your day.</Notice></View>
      {error && <Notice error>{error}</Notice>}
      {loading ? <Loading title="Restoring your session…" /> : session ? <Logbook key={session.user.id} email={session.user.email} /> : <AuthForm />}
    </ScrollView>
  </KeyboardAvoidingView>;
}
