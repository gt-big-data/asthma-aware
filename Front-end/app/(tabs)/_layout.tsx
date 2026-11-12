import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

export const unstable_settings = {
  anchor: "dashboard",
};

export default function TabLayout() {
  const insets = useSafeAreaInsets();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: "#55c7f3",
        tabBarInactiveTintColor: "#8192a7",
        tabBarStyle: {
          backgroundColor: "#0f1928",
          borderTopColor: "#25344a",
          height: 64 + Math.max(insets.bottom, 8),
          paddingTop: 6,
          paddingBottom: Math.max(insets.bottom, 8),
        },
        tabBarLabelStyle: {
          fontSize: 11,
          lineHeight: 16,
          fontWeight: "600",
        },
      }}
    >
      <Tabs.Screen
        name="dashboard"
        options={{
          title: "Dashboard",
          tabBarIcon: ({ focused, size }) => (
            <Ionicons
              name="speedometer-outline"
              color={focused ? "#55c7f3" : "#8192a7"}
              size={size}
            />
          ),
        }}
      />
      <Tabs.Screen
        name="symptoms"
        options={{
          title: "Symptom Log",
          tabBarIcon: ({ focused, size }) => <Ionicons name="clipboard-outline" color={focused ? "#55c7f3" : "#8192a7"} size={size} />,
        }}
      />
      <Tabs.Screen
        name="map"
        options={{
          title: "Map",
          tabBarIcon: ({ focused, size }) => (
            <Ionicons
              name="map-outline"
              color={focused ? "#55c7f3" : "#8192a7"}
              size={size}
            />
          ),
        }}
      />
    </Tabs>
  );
}
