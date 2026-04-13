import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: "rgba(15, 15, 25, 0.95)",
          borderTopColor: "rgba(255,255,255,0.1)",
        },
        tabBarActiveTintColor: "#4fc3f7",
        tabBarInactiveTintColor: "rgba(255,255,255,0.4)",
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Air Quality",
          tabBarIcon: ({ color }) => (
            <Ionicons name="cloudy-outline" size={22} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="socioeconomic"
        options={{
          title: "Equity",
          tabBarIcon: ({ color }) => (
            <Ionicons name="people-outline" size={22} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}
