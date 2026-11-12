import { useState } from "react";
import { Text, TextInput, View } from "react-native";
import { supabase } from "../../lib/supabase";
import { Button, Card, colors, Notice, styles } from "./ui";

export function AuthForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    if (!supabase || busy) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      const credentials = { email: email.trim(), password };
      const result = creating ? await supabase.auth.signUp(credentials) : await supabase.auth.signInWithPassword(credentials);
      if (result.error) { setError(result.error.message); return; }
      setPassword("");
      if (creating && !result.data.session) {
        setMessage("Check your email to confirm your account, then return here to sign in.");
        setCreating(false);
      }
    } catch { setError("Could not connect to sign-in. Please try again."); }
    finally { setBusy(false); }
  }
  if (!supabase) return <Card><Text style={styles.heading}>Sign-in unavailable</Text><Notice>Sign-in is not configured in this build. Please contact the project team.</Notice></Card>;
  return <Card>
    <Text style={styles.heading}>{creating ? "Create your account" : "Sign in to your symptom log"}</Text>
    <Notice>Your entries are saved to your account so you can return and update them.</Notice>
    <Text nativeID="email-label" style={styles.label}>Email</Text>
    <TextInput accessibilityLabel="Email" autoComplete="email" textContentType="emailAddress" autoCapitalize="none" keyboardType="email-address"
      value={email} onChangeText={setEmail} editable={!busy} placeholder="you@example.com" placeholderTextColor={colors.muted} style={styles.input} />
    <Text style={styles.label}>Password</Text>
    <TextInput accessibilityLabel="Password" autoComplete={creating ? "new-password" : "current-password"} textContentType={creating ? "newPassword" : "password"}
      secureTextEntry value={password} onChangeText={setPassword} editable={!busy} autoCapitalize="none" style={styles.input}
      onSubmitEditing={() => { if (email.trim() && password) void submit(); }} />
    {error && <Notice error>{error}</Notice>}
    {message && <Notice>{message}</Notice>}
    <Button title={busy ? "Please wait…" : creating ? "Create account" : "Sign in"} disabled={busy || !email.trim() || !password} onPress={() => void submit()} />
    <View><Button secondary disabled={busy} title={creating ? "Already have an account? Sign in" : "New here? Create an account"}
      onPress={() => { setCreating(!creating); setError(null); setMessage(null); }} /></View>
  </Card>;
}
