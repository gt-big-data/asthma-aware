import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";

export const unstable_settings = {
  anchor: "dashboard",
};

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: "#55c7f3",
        tabBarInactiveTintColor: "#8192a7",
        tabBarStyle: {
          backgroundColor: "#0f1928",
          borderTopColor: "#25344a",
          height: 62,
          paddingTop: 7,
          paddingBottom: 7,
        },
        tabBarLabelStyle: {
          fontSize: 11,
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
