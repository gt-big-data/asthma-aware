import { useMemo } from "react";
import { StyleSheet, View } from "react-native";
import HeatmapView from "../../components/HeatmapView";
import { LatLngIntensity } from "../../constants/data";

const GEORGIA_BORDER: [number, number][] = [
  [34.98, -85.0],
  [34.99, -83.1],
  [34.96, -82.7],
  [34.92, -82.1],
  [34.88, -81.0],
  [34.55, -81.0],
  [34.2, -81.0],
  [33.8, -81.0],
  [33.4, -81.0],
  [33.0, -81.0],
  [32.6, -81.1],
  [32.2, -81.2],
  [31.8, -81.3],
  [31.4, -81.4],
  [31.0, -81.5],
  [30.7, -81.6],
  [30.36, -81.6],
  [30.36, -84.9],
  [30.6, -85.0],
  [31.0, -85.0],
  [31.4, -85.0],
  [31.8, -85.0],
  [32.2, -85.0],
  [32.6, -85.0],
  [33.0, -85.0],
  [33.4, -85.1],
  [33.8, -85.2],
  [34.2, -85.3],
  [34.6, -85.3],
  [34.9, -85.2],
];

function isInsideGeorgia(lat: number, lon: number): boolean {
  let inside = false;

  for (
    let i = 0, j = GEORGIA_BORDER.length - 1;
    i < GEORGIA_BORDER.length;
    j = i, i += 1
  ) {
    const [latI, lonI] = GEORGIA_BORDER[i];
    const [latJ, lonJ] = GEORGIA_BORDER[j];
    const crosses =
      (lonI > lon) !== (lonJ > lon) &&
      lat < ((latJ - latI) * (lon - lonI)) / (lonJ - lonI) + latI;
    if (crosses) inside = !inside;
  }

  return inside;
}

function makeTemporaryGeorgiaData(): LatLngIntensity[] {
  const points: LatLngIntensity[] = [];
  const step = 0.075;

  for (let lat = 30.4; lat <= 35; lat += step) {
    for (let lon = -85.5; lon <= -80.9; lon += step) {
      if (!isInsideGeorgia(lat, lon)) continue;

      const atlanta = Math.exp(
        -(((lat - 33.75) / 0.65) ** 2 + ((lon + 84.39) / 0.72) ** 2),
      );
      const macon = Math.exp(
        -(((lat - 32.84) / 0.55) ** 2 + ((lon + 83.63) / 0.6) ** 2),
      );
      const savannah = Math.exp(
        -(((lat - 32.08) / 0.5) ** 2 + ((lon + 81.1) / 0.5) ** 2),
      );
      const regionalVariation =
        (Math.sin(lat * 3.1 + lon * 2.3) + 1) * 0.04;
      const intensity = 0.12 + atlanta * 0.62 + macon * 0.28 +
        savannah * 0.2 + regionalVariation;

      points.push([lat, lon, intensity]);
    }
  }

  return points;
}

export default function MapScreen() {
  const points = useMemo(() => makeTemporaryGeorgiaData(), []);

  return (
    <View style={styles.root}>
      <HeatmapView points={points} visualization="heat" />
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#1a1a2e",
  },
});