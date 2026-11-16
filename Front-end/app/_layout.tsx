import { StatusBar } from "expo-status-bar";
import { Stack } from "expo-router";
import { AuthProvider } from "../providers/AuthProvider";
import { ForecastProvider } from "../providers/ForecastProvider";

export default function RootLayout() {
  return <AuthProvider><ForecastProvider><StatusBar style="dark" /><Stack screenOptions={{ headerShown: false }} /></ForecastProvider></AuthProvider>;
}
