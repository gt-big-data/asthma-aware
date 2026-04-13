import { useRef, useEffect, useCallback } from "react";
import { StyleSheet, View, TouchableOpacity, Text } from "react-native";
import { WebView, WebViewMessageEvent } from "react-native-webview";
import { LatLngIntensity } from "../constants/data";

interface HeatmapViewProps {
  points: LatLngIntensity[];
}

// Atlanta grid constants (must match backend constants.py):
//   lat: 33.39 – 33.64  (56 rows)   → lat_step = 0.25 / 56
//   lon: -84.55 – -84.28 (96 cols)  → lon_step = 0.27 / 96
const MAP_HTML = `
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body, #map { width: 100%; height: 100%; }
    .leaflet-control-attribution { display: none; }
    body { background: #1a1a2e; }
  </style>
</head>
<body>
  <div id="map"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

  <script>
    const STOPS = [
      [0.00, [0,   0,   128]],
      [0.20, [0,   0,   255]],
      [0.40, [0,   229, 255]],
      [0.55, [0,   255,   0]],
      [0.70, [255, 255,   0]],
      [0.85, [255, 165,   0]],
      [0.95, [255,  69,   0]],
      [1.00, [255,   0,   0]],
    ];

    function getColor(t) {
      t = Math.max(0, Math.min(1, t));
      for (let i = 1; i < STOPS.length; i++) {
        if (t <= STOPS[i][0]) {
          const [t0, c0] = STOPS[i - 1];
          const [t1, c1] = STOPS[i];
          const f = (t - t0) / (t1 - t0);
          return [
            Math.round(c0[0] + f * (c1[0] - c0[0])),
            Math.round(c0[1] + f * (c1[1] - c0[1])),
            Math.round(c0[2] + f * (c1[2] - c0[2])),
          ];
        }
      }
      return [255, 0, 0];
    }

    const LAT_STEP = 0.25 / 56;
    const LON_STEP = 0.27 / 96;

    let map = null;
    let cellLayer = null;
    const renderer = L.canvas({ padding: 0.5 });

    try {
      map = L.map("map", {
        zoomControl: false,
        attributionControl: false,
        dragging: true,
        touchZoom: true,
        scrollWheelZoom: true,
        doubleClickZoom: true,
        tap: true,
      });

      L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 18,
      }).addTo(map);

      map.setView([33.515, -84.415], 11);
    } catch (err) {
      console.error("Map init failed:", err.message);
    }

    // Fade out the overlay as the user zooms in so the base map stays readable.
    // zoom 11 (default fit) → 0.82 opacity; each extra zoom level drops ~0.10.
    function updateLayerOpacity() {
      if (!renderer._container) return;
      const z = map.getZoom();
      const opacity = Math.max(0.20, Math.min(0.82, 0.82 - (z - 11) * 0.10));
      renderer._container.style.opacity = opacity;
    }
    map.on("zoomend", updateLayerOpacity);

    // Called by React Native zoom buttons
    window.mapZoomIn  = function() { map && map.zoomIn();  };
    window.mapZoomOut = function() { map && map.zoomOut(); };

    window.updateHeatmap = function(points) {
      try {
        if (!points || points.length === 0) return;

        if (cellLayer) {
          map.removeLayer(cellLayer);
          cellLayer = null;
        }

        const vals   = points.map(p => p[2]);
        const minVal = Math.min(...vals);
        const maxVal = Math.max(...vals);
        const range  = maxVal - minVal;

        cellLayer = L.layerGroup();

        for (let i = 0; i < points.length; i++) {
          const lat = points[i][0];
          const lon = points[i][1];
          const raw = points[i][2];
          const t   = range > 0.0001 ? (raw - minVal) / range : 0.5;

          const [r, g, b] = getColor(t);

          L.rectangle(
            [[lat - LAT_STEP, lon], [lat, lon + LON_STEP]],
            {
              renderer,
              weight: 0,
              fillColor: "rgb(" + r + "," + g + "," + b + ")",
              fillOpacity: 0.82,
            }
          ).addTo(cellLayer);
        }

        cellLayer.addTo(map);

        const lats = points.map(p => p[0]);
        const lngs = points.map(p => p[1]);
        map.fitBounds(
          L.latLngBounds(
            L.latLng(Math.min(...lats) - LAT_STEP, Math.min(...lngs)),
            L.latLng(Math.max(...lats),             Math.max(...lngs) + LON_STEP)
          ),
          { padding: [20, 20], maxZoom: 11, animate: false }
        );
      } catch (err) {
        console.error("updateHeatmap error:", err.message);
      }
    };

    window.ReactNativeWebView.postMessage("ready");
  </script>
</body>
</html>
`;

export default function HeatmapView({ points }: HeatmapViewProps) {
  const webviewRef   = useRef<WebView>(null);
  const webviewReady = useRef(false);
  const pendingPts   = useRef<LatLngIntensity[] | null>(null);

  const injectHeatmap = useCallback((pts: LatLngIntensity[]) => {
    webviewRef.current?.injectJavaScript(
      `window.updateHeatmap(${JSON.stringify(pts)}); true;`
    );
  }, []);

  const zoomIn  = useCallback(() => {
    webviewRef.current?.injectJavaScript("window.mapZoomIn(); true;");
  }, []);

  const zoomOut = useCallback(() => {
    webviewRef.current?.injectJavaScript("window.mapZoomOut(); true;");
  }, []);

  const onMessage = useCallback((event: WebViewMessageEvent) => {
    if (event.nativeEvent.data === "ready") {
      webviewReady.current = true;
      if (pendingPts.current && pendingPts.current.length > 0) {
        injectHeatmap(pendingPts.current);
        pendingPts.current = null;
      }
    }
  }, [injectHeatmap]);

  useEffect(() => {
    if (!points || points.length === 0) return;
    if (webviewReady.current) {
      injectHeatmap(points);
    } else {
      pendingPts.current = points;
    }
  }, [points, injectHeatmap]);

  return (
    <View style={StyleSheet.absoluteFill}>
      <WebView
        ref={webviewRef}
        source={{ html: MAP_HTML }}
        style={StyleSheet.absoluteFill}
        originWhitelist={["*"]}
        javaScriptEnabled
        domStorageEnabled
        scrollEnabled={false}
        overScrollMode="never"
        mixedContentMode="always"
        onMessage={onMessage}
      />

      {/* React Native zoom buttons — always on top, never blocked by WebView */}
      <View style={styles.zoomControls} pointerEvents="box-none">
        <TouchableOpacity style={styles.zoomBtn} onPress={zoomIn} activeOpacity={0.7}>
          <Text style={styles.zoomText}>+</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.zoomBtn} onPress={zoomOut} activeOpacity={0.7}>
          <Text style={styles.zoomText}>−</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  zoomControls: {
    position: "absolute",
    top: 100,
    right: 14,
    gap: 8,
  },
  zoomBtn: {
    width: 36,
    height: 36,
    borderRadius: 8,
    backgroundColor: "rgba(15,15,25,0.85)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.2)",
    alignItems: "center",
    justifyContent: "center",
  },
  zoomText: {
    color: "#ffffff",
    fontSize: 20,
    fontWeight: "400",
    lineHeight: 22,
  },
});
