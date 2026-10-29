import { useRef, useEffect, useCallback, useState } from "react";
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
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.css"/>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body, #map { width: 100%; height: 100%; }
    body { background: #1a1a2e; }
  </style>
</head>
<body>
  <div id="map"></div>

  <script src="https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.js"></script>

  <script>
    // MapLibre draws both the base map and the data, so they move together
    // in the same frame while zooming (no drift between two map engines).

    // Metro Atlanta: roughly Marietta to Stockbridge, Douglasville to Lawrenceville.
    const ATLANTA_BOUNDS = [[-84.80, 33.45], [-83.95, 34.05]];

    // Fallback cell size when the grid spacing can't be worked out from the data.
    const DEFAULT_LAT_STEP = 0.25 / 56;
    const DEFAULT_LON_STEP = 0.27 / 96;

    // Heat blob radius, as a multiple of the grid spacing.
    const RADIUS_PER_STEP = 2;

    // Largest heat blob we allow on screen, and the normal max zoom.
    const MAX_RADIUS_PX = 1000;
    const MAX_ZOOM = 15;

    const CELL_COLORS = [
      0.00, "rgb(0,0,128)",
      0.20, "rgb(0,0,255)",
      0.40, "rgb(0,229,255)",
      0.55, "rgb(0,255,0)",
      0.70, "rgb(255,255,0)",
      0.85, "rgb(255,165,0)",
      0.95, "rgb(255,69,0)",
      1.00, "rgb(255,0,0)",
    ];

    let map = null;
    let mapLoaded = false;
    let pending = null;
    let mode = "heat";

    function post(message) {
      if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(message);
    }

    // Smallest gap between neighbouring values, i.e. the grid spacing.
    function gridStep(values, fallback) {
      const sorted = Array.from(new Set(values)).sort((a, b) => a - b);
      let step = Infinity;
      for (let i = 1; i < sorted.length; i++) {
        const gap = sorted[i] - sorted[i - 1];
        if (gap > 1e-6 && gap < step) step = gap;
      }
      return Number.isFinite(step) ? step : fallback;
    }

    function setLayerVisibility() {
      if (!mapLoaded || !map.getLayer("heat")) return;
      map.setLayoutProperty("heat", "visibility", mode === "heat" ? "visible" : "none");
      map.setLayoutProperty("cells", "visibility", mode === "cells" ? "visible" : "none");
    }

    function drawData(points) {
      const lats = points.map(p => p[0]);
      const lons = points.map(p => p[1]);
      const vals = points.map(p => p[2]);
      const minVal = Math.min(...vals);
      const range = Math.max(...vals) - minVal;
      const latStep = gridStep(lats, DEFAULT_LAT_STEP);
      const lonStep = gridStep(lons, DEFAULT_LON_STEP);

      const pointFeatures = [];
      const cellFeatures = [];
      for (const [lat, lon, raw] of points) {
        const t = range > 0.0001 ? (raw - minVal) / range : 0.5;
        pointFeatures.push({
          type: "Feature",
          properties: { t },
          geometry: { type: "Point", coordinates: [lon, lat] },
        });
        // Each point is the north-west corner of its cell.
        cellFeatures.push({
          type: "Feature",
          properties: { t },
          geometry: {
            type: "Polygon",
            coordinates: [[
              [lon, lat],
              [lon + lonStep, lat],
              [lon + lonStep, lat - latStep],
              [lon, lat - latStep],
              [lon, lat],
            ]],
          },
        });
      }

      // Blob radius in metres, then converted to screen pixels at zoom 0.
      // Doubling it at every zoom level keeps each blob the same size on
      // the ground, so the data doesn't spread out or shrink while zooming.
      const midLat = (Math.min(...lats) + Math.max(...lats)) / 2;
      const cosLat = Math.cos(midLat * Math.PI / 180);
      const latStepM = latStep * 111320;
      const lonStepM = lonStep * 111320 * cosLat;
      const stepM = Math.sqrt(latStepM * lonStepM);
      const radiusM = stepM * RADIUS_PER_STEP;
      const metresPerPixelAtZ0 = 78271.517 * cosLat;
      const radiusAtZ0 = radiusM / metresPerPixelAtZ0;

      // MapLibre's heatmap breaks once a blob gets bigger than roughly 1500px,
      // so stop zooming in before that. Past this point the screen is inside
      // a single grid cell anyway, so there's nothing more to see.
      const dataMaxZoom = Math.log2(MAX_RADIUS_PX / radiusAtZ0);
      map.setMaxZoom(Math.min(MAX_ZOOM, Math.max(8, dataMaxZoom)));

      // Scale blob strength so a fully "hot" area adds up to a density of 1.
      // (MapLibre's blob is a Gaussian whose radius is 3 standard deviations.)
      // Overlap between blobs is the same at every zoom, so colours stay put.
      const intensity = 1 / (0.3989 * (2 * Math.PI / 9) * RADIUS_PER_STEP * RADIUS_PER_STEP);

      const pointData = { type: "FeatureCollection", features: pointFeatures };
      const cellData = { type: "FeatureCollection", features: cellFeatures };

      if (map.getSource("points")) {
        map.getSource("points").setData(pointData);
        map.getSource("cells").setData(cellData);
        map.setPaintProperty("heat", "heatmap-radius", radiusExpression(radiusAtZ0));
        map.setPaintProperty("heat", "heatmap-intensity", intensity);
      } else {
        // Large tile buffer so blobs near a tile edge aren't cut off.
        map.addSource("points", { type: "geojson", data: pointData, buffer: 512, maxzoom: 9 });
        map.addSource("cells", { type: "geojson", data: cellData });

        map.addLayer({
          id: "cells",
          type: "fill",
          source: "cells",
          paint: {
            "fill-color": ["interpolate", ["linear"], ["get", "t"], ...CELL_COLORS],
            "fill-opacity": ["interpolate", ["linear"], ["zoom"], 11, 0.57, 15.3, 0.14],
            "fill-antialias": false,
          },
        });

        map.addLayer({
          id: "heat",
          type: "heatmap",
          source: "points",
          paint: {
            // Low values still show as faint blue instead of disappearing.
            "heatmap-weight": ["+", 0.15, ["*", 0.85, ["get", "t"]]],
            "heatmap-intensity": intensity,
            "heatmap-radius": radiusExpression(radiusAtZ0),
            "heatmap-opacity": 0.65,
            "heatmap-color": [
              "interpolate", ["linear"], ["heatmap-density"],
              0.00, "rgba(37,71,168,0)",
              0.08, "rgba(37,71,168,0.5)",
              0.15, "#2547a8",
              0.35, "#20b9c7",
              0.55, "#69cf71",
              0.75, "#f4d84a",
              1.00, "#e53935",
            ],
          },
        });
      }
      setLayerVisibility();
    }

    function radiusExpression(radiusAtZ0) {
      return ["interpolate", ["exponential", 2], ["zoom"],
        0, radiusAtZ0,
        20, radiusAtZ0 * Math.pow(2, 20)];
    }

    try {
      map = new maplibregl.Map({
        container: "map",
        style: "https://tiles.openfreemap.org/styles/positron",
        bounds: ATLANTA_BOUNDS,
        minZoom: 5,
        maxZoom: MAX_ZOOM,
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        attributionControl: { compact: true },
      });
      map.touchZoomRotate.disableRotation();

      map.on("load", () => {
        // On phones the WebView can still be resizing when the map is created,
        // which makes the first fit zoom out too far. Fit again once loaded.
        map.resize();
        map.fitBounds(ATLANTA_BOUNDS, { animate: false });
        mapLoaded = true;
        if (pending) {
          drawData(pending);
          pending = null;
        }
        post("ready");
      });
    } catch (err) {
      console.error("Map init failed:", err.message);
    }

    window.mapZoomIn  = function() { map && map.zoomIn();  };
    window.mapZoomOut = function() { map && map.zoomOut(); };

    window.setMapMode = function(nextMode) {
      mode = nextMode === "cells" ? "cells" : "heat";
      setLayerVisibility();
    };

    window.updateHeatmap = function(points, showAsHeatmap) {
      try {
        if (!points || points.length === 0 || !map) return;
        mode = showAsHeatmap ? "heat" : "cells";
        if (!mapLoaded) {
          pending = points;
          return;
        }
        drawData(points);
      } catch (err) {
        console.error("updateHeatmap error:", err.message);
      }
    };
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
  const [mode, setMode] = useState(visualization);
  const modeRef      = useRef(visualization);

  const injectHeatmap = useCallback((pts: LatLngIntensity[]) => {
    webviewRef.current?.injectJavaScript(
      `window.updateHeatmap(${JSON.stringify(pts)}, ${modeRef.current === "heat"}); true;`
    );
  }, []);

  const changeMode = useCallback((next: "cells" | "heat") => {
    modeRef.current = next;
    setMode(next);
    webviewRef.current?.injectJavaScript(`window.setMapMode("${next}"); true;`);
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

      {/* Switch between the smooth heatmap and the fixed grid squares */}
      <View style={styles.modeToggle}>
        {(["heat", "cells"] as const).map((option) => (
          <TouchableOpacity
            key={option}
            style={[styles.modeBtn, mode === option && styles.modeBtnActive]}
            onPress={() => changeMode(option)}
            activeOpacity={0.7}
            accessibilityRole="button"
            accessibilityState={{ selected: mode === option }}
          >
            <Text style={[styles.modeText, mode === option && styles.modeTextActive]}>
              {option === "heat" ? "Heat" : "Grid"}
            </Text>
          </TouchableOpacity>
        ))}
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
  modeToggle: {
    position: "absolute",
    top: 190,
    right: 14,
    borderRadius: 8,
    overflow: "hidden",
    backgroundColor: "rgba(15,15,25,0.85)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.2)",
  },
  modeBtn: {
    width: 52,
    paddingVertical: 7,
    alignItems: "center",
  },
  modeBtnActive: {
    backgroundColor: "#55c7f3",
  },
  modeText: {
    color: "#ffffff",
    fontSize: 12,
    fontWeight: "600",
  },
  modeTextActive: {
    color: "#0f1928",
  },
});
