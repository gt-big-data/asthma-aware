import { Platform } from "react-native";
import { createApiClient } from "./api-client";
import { supabase } from "./supabase";

export const api = createApiClient({
  baseUrl: process.env.EXPO_PUBLIC_API_URL
    || (Platform.OS === "android" ? "http://10.0.2.2:8000" : "http://127.0.0.1:8000"),
  async getAccessToken() {
    if (!supabase) return null;
    const { data, error } = await supabase.auth.getSession();
    if (error) return null;
    return data.session?.access_token ?? null;
  },
});
