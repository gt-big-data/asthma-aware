import type { ReactNode } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import LoadingFeedback from "../LoadingFeedback";

export const colors = { background: "#f4f7f4", card: "#ffffff", border: "#dce5de", text: "#1f2d25", muted: "#66766b", accent: "#2d6a4f", error: "#b42318", success: "#39845c" };
export function Button({ title, onPress, disabled = false, secondary = false }: {
  title: string; onPress: () => void; disabled?: boolean; secondary?: boolean;
}) {
  return <Pressable accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled} onPress={onPress}
    style={({ pressed }) => [styles.button, secondary && styles.secondary, (pressed || disabled) && { opacity: 0.55 }]}>
    <Text style={[styles.buttonText, secondary && { color: colors.accent }]}>{title}</Text>
  </Pressable>;
}
export function Card({ children }: { children: ReactNode }) { return <View style={styles.card}>{children}</View>; }
export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <Text accessibilityLiveRegion="polite" style={[styles.note, error && { color: colors.error }]}>{children}</Text>;
}
export function Loading({ title }: { title: string }) {
  return <View style={styles.loading}><LoadingFeedback label={title} /></View>;
}
export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { paddingHorizontal: 18, paddingTop: 24, paddingBottom: 40, gap: 16, width: "100%", maxWidth: 680, alignSelf: "center" },
  card: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderRadius: 20, padding: 18, gap: 14 },
  eyebrow: { color: colors.accent, fontSize: 10, fontWeight: "800", letterSpacing: 2 },
  title: { color: colors.text, fontSize: 28, fontWeight: "700", letterSpacing: -0.5 },
  heading: { color: colors.text, fontSize: 18, fontWeight: "600" },
  label: { color: colors.text, fontSize: 14, fontWeight: "600" },
  note: { color: colors.muted, fontSize: 13, lineHeight: 20 },
  input: { backgroundColor: colors.background, borderColor: colors.border, borderWidth: 1, borderRadius: 12, padding: 14, color: colors.text, fontSize: 16, minHeight: 48 },
  button: { backgroundColor: colors.accent, borderRadius: 12, padding: 14, minHeight: 48, alignItems: "center", justifyContent: "center" },
  secondary: { backgroundColor: "transparent", borderColor: colors.border, borderWidth: 1 },
  buttonText: { color: colors.background, fontSize: 14, fontWeight: "700" },
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  loading: { paddingVertical: 30, alignItems: "center", gap: 12 },
});
