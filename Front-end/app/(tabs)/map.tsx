import { useEffect, useMemo, useState } from "react";
import { StyleSheet, View } from "react-native";
import HeatmapView, {
  ATLANTA_BOUNDS,
  MapBounds,
} from "../../components/HeatmapView";
import { LatLngIntensity } from "../../constants/data";
import {
  Coords,
  getCachedUserLocation,
  getUserLocation,
} from "../../constants/userLocation";

// Same size of area as the metro Atlanta view (about 70 km across).
const HALF_SPAN_LAT = 0.3;
const HALF_SPAN_LON = 0.425;

// If no location has been found yet, wait at most this long before opening
// on Atlanta. Usually the dashboard has already found it by now.
const LOCATION_WAIT_MS = 1000;

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

// Is the location inside the area we have data for? If not (e.g. the user
// is outside Georgia), there'd be nothing to see, so we open on Atlanta.
function isInDataArea(coords: Coords, points: LatLngIntensity[]): boolean {
  if (points.length === 0) return false;
  const lats = points.map((p) => p[0]);
  const lons = points.map((p) => p[1]);
  return (
    coords.latitude >= Math.min(...lats) &&
    coords.latitude <= Math.max(...lats) &&
    coords.longitude >= Math.min(...lons) &&
    coords.longitude <= Math.max(...lons)
  );
}

function boundsAround(coords: Coords): MapBounds {
  return [
    [coords.longitude - HALF_SPAN_LON, coords.latitude - HALF_SPAN_LAT],
    [coords.longitude + HALF_SPAN_LON, coords.latitude + HALF_SPAN_LAT],
  ];
}

export default function MapScreen() {
  const points = useMemo(() => makeTemporaryGeorgiaData(), []);

  // undefined = still looking, null = no location (use Atlanta).
  const [location, setLocation] = useState<Coords | null | undefined>(
    () => getCachedUserLocation() ?? undefined,
  );

  useEffect(() => {
    if (location !== undefined) return;
    let active = true;
    const timeout = setTimeout(() => {
      if (active) setLocation(null);
    }, LOCATION_WAIT_MS);

    getUserLocation().then((coords) => {
      if (active) setLocation(coords);
    });

    return () => {
      active = false;
      clearTimeout(timeout);
    };
  }, [location]);

  // The map is only created once we know where to start, so it never jumps.
  const startBounds =
    location && isInDataArea(location, points)
      ? boundsAround(location)
      : ATLANTA_BOUNDS;

  return (
    <View style={styles.root}>
      {location !== undefined && (
        <HeatmapView
          points={points}
          visualization="heat"
          startBounds={startBounds}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#1a1a2e",
  },
});