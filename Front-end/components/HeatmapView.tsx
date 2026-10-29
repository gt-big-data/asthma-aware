import { useRef, useEffect, useCallback } from "react";
import { StyleSheet, View, TouchableOpacity, Text } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { LatLngIntensity } from "../constants/data";

interface HeatmapViewProps {
  points: LatLngIntensity[];
  visualization?: "cells" | "heat";
}

const MAP_HTML = `
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.css"/>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body, #map { width: 100%; height: 100%; }
    body { background: #1a1a2e; }
  </style>
</head>
<body>
  <div id="map"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.js"></script>
  <script src="https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js"></script>
  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>

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
    let heatLayer = null;
    const renderer = L.canvas({ padding: 0.5 });

    try {
      map = L.map("map", {
        zoomControl: false,
        attributionControl: true,
        minZoom: 5,
        maxZoom: 15,
        dragging: true,
        touchZoom: true,
        scrollWheelZoom: true,
        doubleClickZoom: true,
        zoomAnimation: true,
        tap: true,
      });

      MaplibreGLLeaflet.maplibreGL({
        style: "https://tiles.openfreemap.org/styles/positron",
      }).addTo(map);
      map.attributionControl.addAttribution(
        '<a href="https://openfreemap.org">OpenFreeMap</a> | <a href="https://www.openmaptiles.org/">© OpenMapTiles</a> | Data from <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      );

      // Start on metro Atlanta (roughly Marietta to Stockbridge,
      // Douglasville to Lawrenceville). Fitting bounds instead of a fixed
      // zoom keeps the same area in view on any screen size.
      map.fitBounds(
        [[33.45, -84.80], [34.05, -83.95]],
        { animate: false }
      );
    } catch (err) {
      console.error("Map init failed:", err.message);
    }

    function updateLayerOpacity() {
      const z = map.getZoom();
      if (renderer._container) {
        renderer._container.style.opacity =
          Math.max(0.14, Math.min(0.57, 0.57 - (z - 11) * 0.10));
      }
    }
    map.on("zoomend", updateLayerOpacity);

    window.mapZoomIn  = function() { map && map.zoomIn();  };
    window.mapZoomOut = function() { map && map.zoomOut(); };

    window.updateHeatmap = function(points, showAsHeatmap) {
      try {
        if (!points || points.length === 0 || !map) return;

        if (cellLayer) {
          map.removeLayer(cellLayer);
          cellLayer = null;
        }
        if (heatLayer) {
          map.removeLayer(heatLayer);
          heatLayer = null;
        }

        const vals   = points.map(p => p[2]);
        const minVal = Math.min(...vals);
        const maxVal = Math.max(...vals);
        const range  = maxVal - minVal;

        if (showAsHeatmap) {
          const heatPoints = points.map(point => [
            point[0],
            point[1],
            range > 0.0001 ? (point[2] - minVal) / range : 0.5,
          ]);
          heatLayer = L.heatLayer(heatPoints, {
            radius: 22,
            blur: 24,
            maxZoom: 15,
            minOpacity: 0.28,
            gradient: {
              0.15: "#2547a8",
              0.35: "#20b9c7",
              0.55: "#69cf71",
              0.75: "#f4d84a",
              1: "#e53935",
            },
          }).addTo(map);
          updateLayerOpacity();
          return;
        }

        cellLayer = L.layerGroup();
        for (let i = 0; i < points.length; i++) {
          const [lat, lon, raw] = points[i];
          const t = range > 0.0001 ? (raw - minVal) / range : 0.5;
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
        updateLayerOpacity();
      } catch (err) {
        console.error("updateHeatmap error:", err.message);
      }
    };

    window.ReactNativeWebView.postMessage("ready");
  </script>
</body>
</html>
`;

export default function HeatmapView({
  points,
  visualization = "cells",
}: HeatmapViewProps) {
  const webviewRef   = useRef<WebView<object>>(null);
  const webviewReady = useRef(false);
  const pendingPts   = useRef<LatLngIntensity[] | null>(null);

  const injectHeatmap = useCallback((pts: LatLngIntensity[]) => {
    webviewRef.current?.injectJavaScript(
      `window.updateHeatmap(${JSON.stringify(pts)}, ${visualization === "heat"}); true;`
    );
  }, [visualization]);

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
      <WebView<object>
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
