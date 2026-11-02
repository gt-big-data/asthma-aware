import type { Session } from "@supabase/supabase-js";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { AppState, Platform } from "react-native";
import { supabase } from "../lib/supabase";

const AuthContext = createContext<{ session: Session | null; loading: boolean; error: string | null }>({
  session: null, loading: true, error: null,
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(!!supabase);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const client = supabase;
    if (!client) return;
    let active = true;
    let version = 0;
    const { data: { subscription } } = client.auth.onAuthStateChange((_event, next) => {
      version += 1;
      if (active) { setSession(next); setLoading(false); setError(null); }
    });
    client.auth.getSession().then(({ data, error: failure }) => {
      if (!active || version !== 0) return;
      setSession(data.session); setLoading(false);
      if (failure) setError("Could not restore your session. Please sign in again.");
    }).catch(() => { if (active) { setLoading(false); setError("Could not restore your session. Please sign in again."); } });
    if (Platform.OS !== "web" && AppState.currentState === "active") client.auth.startAutoRefresh();
    const listener = Platform.OS === "web" ? null : AppState.addEventListener("change", (state) => {
      if (state === "active") client.auth.startAutoRefresh(); else client.auth.stopAutoRefresh();
    });
    return () => { active = false; subscription.unsubscribe(); listener?.remove(); if (Platform.OS !== "web") client.auth.stopAutoRefresh(); };
  }, []);
  return <AuthContext.Provider value={{ session, loading, error }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
