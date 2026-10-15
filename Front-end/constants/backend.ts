/**
 * Where the AsthmaAware backend lives.
 *
 * Shared by the Air Quality and Equity views so the two cannot drift apart.
 *
 * Under Expo Go the backend runs on the same machine as the Metro dev server,
 * so we reuse the host Expo has already connected to. That covers simulators,
 * the Android emulator and physical devices on the same Wi-Fi without anyone
 * editing an IP by hand.
 *
 * Caveat: `expo start --tunnel` points hostUri at an exp.direct address that
 * only forwards Metro's port, not 8000. Use LAN mode, or expose the backend
 * through its own tunnel and set EXPO_PUBLIC_BACKEND_URL.
 */

import Constants from "expo-constants";
import { Platform } from "react-native";

const BACKEND_PORT = 8000;

// Escape hatch: set EXPO_PUBLIC_BACKEND_URL to override everything below.
const override = process.env.EXPO_PUBLIC_BACKEND_URL;

// "192.168.1.5:8081" on LAN, "127.0.0.1:8081" for a simulator.
const devHost = Constants.expoConfig?.hostUri?.split(":")[0];

// Only reached when hostUri is unavailable (a production build).
// Android emulator: the host machine is 10.0.2.2. Everywhere else: loopback.
const fallbackHost = Platform.OS === "android" ? "10.0.2.2" : "127.0.0.1";

export const BACKEND_BASE_URL =
  override ?? `http://${devHost ?? fallbackHost}:${BACKEND_PORT}`;
