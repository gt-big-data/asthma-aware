import "react-native-url-polyfill/auto";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { createClient } from "@supabase/supabase-js";
import { Platform } from "react-native";

const url = process.env.EXPO_PUBLIC_SUPABASE_URL;
const key = process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
export const supabase = url && key?.startsWith("sb_publishable_")
  ? createClient(url, key, {
    auth: {
      ...(Platform.OS !== "web" ? { storage: AsyncStorage } : {}),
      persistSession: true, autoRefreshToken: true, detectSessionInUrl: false,
    },
  }) : null;
