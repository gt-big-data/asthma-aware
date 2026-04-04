import { useRef, useEffect } from "react";
import { StyleSheet } from "react-native";
import { WebView } from "react-native-webview";
import { LatLngIntensity } from "../constants/data";

interface HeatmapViewProps {
  points: LatLngIntensity[];
  centerLat?: number;
  centerLng?: number;
  zoom?: number;
}

const MAP_HTML = `
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body, #map { width: 100%; height: 100%; }
    .leaflet-control-attribution { display: none; }
    body { background: #1a1a2e; }
    #debug {
      position: absolute;
      bottom: 10px;
      left: 10px;
      background: rgba(0, 0, 0, 0.85);
      color: #0f0;
      padding: 10px;
      border-radius: 4px;
      font-family: monospace;
      font-size: 10px;
      max-width: 280px;
      z-index: 1000;
      display: none;
      white-space: pre-wrap;
      word-break: break-all;
      max-height: 200px;
      overflow-y: auto;
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="debug"></div>
  
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
  
  <script>
    let map = null;
    let heatLayer = null;
    const debugEl = document.getElementById("debug");
    
    function log(msg) {
      console.log("[HeatmapView] " + msg);
      debugEl.textContent += msg + "\\n";
    }
    
    try {
      // Initialize map
      map = L.map("map", {
        zoomControl: false,
        attributionControl: false,
        dragging: true,
        touchZoom: true,
        scrollWheelZoom: true,
        doubleClickZoom: true,
      });

      L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 18,
      }).addTo(map);

      log("Map initialized");
    } catch (err) {
      console.error("Map init failed:", err.message);
    }

    window.updateHeatmap = function(lat, lng, zoom, points) {
      try {
        if (!points || points.length === 0) {
          log("No points provided");
          map.setView([lat, lng], zoom);
          return;
        }

        // Remove old layer
        if (heatLayer) {
          map.removeLayer(heatLayer);
        }

        log("Rendering " + points.length + " points");

        // Calculate statistics
        const intensities = points.map(p => p[2]);
        const minIntensity = Math.min(...intensities);
        const maxIntensity = Math.max(...intensities);
        const avgIntensity = intensities.reduce((a, b) => a + b, 0) / intensities.length;
        const rangeIntensity = maxIntensity - minIntensity;

        log("Raw intensity: [" + minIntensity.toFixed(3) + ", " + maxIntensity.toFixed(3) + "]");
        log("Range: " + rangeIntensity.toFixed(3) + " (avg: " + avgIntensity.toFixed(3) + ")");

        // NORMALIZE: Stretch the data range to 0-1
        // This ensures SO2 (0.098-0.228) and NO2 (0.008-0.106) use full color spectrum
        let normalizedPoints;
        if (rangeIntensity > 0.0001) {
          // Map [min, max] to [0, 1]
          normalizedPoints = points.map(p => [
            p[0],
            p[1],
            (p[2] - minIntensity) / rangeIntensity  // Normalize to 0-1
          ]);
          log("Normalized: range stretched to [0, 1]");
        } else {
          // All same value - make it visible
          normalizedPoints = points.map(p => [p[0], p[1], 0.5]);
          log("All same value - normalized to 0.5");
        }

        // Get normalized stats
        const normalizedIntensities = normalizedPoints.map(p => p[2]);
        const normalizedMin = Math.min(...normalizedIntensities);
        const normalizedMax = Math.max(...normalizedIntensities);
        log("Norm range: [" + normalizedMin.toFixed(3) + ", " + normalizedMax.toFixed(3) + "]");

        // Create heatmap layer with normalized data
        heatLayer = L.heatLayer(normalizedPoints, {
          radius: 5,        // Smaller = sharper hot spots
          blur: 10,          // Less blur = clearer definition
          maxZoom: 10,
          max: 1.0,          // Since data is normalized to 0-1
          minOpacity: 0.1,   // Lower opacity = see the map underneath
          gradient: {
            0.3:  "#000080",  // Dark blue (low)
            0.4: "#0000ff",  // Blue
            0.5:  "#00e5ff",  // Cyan
            0.6: "#00ff00",  // Green
            0.7:  "#ffff00",  // Yellow
            0.8: "#ffa500",  // Orange
            0.9:  "#ff4500",  // Orange-red
            1.0:  "#ff0000",  // Red (high)
          },
        }).addTo(map);

        log("Heatmap layer added (opacity: 0.35)");

        // Fit map to data bounds
        try {
          const lats = points.map(p => p[0]);
          const lngs = points.map(p => p[1]);
          const minLat = Math.min(...lats);
          const maxLat = Math.max(...lats);
          const minLng = Math.min(...lngs);
          const maxLng = Math.max(...lngs);

          const dataBounds = L.latLngBounds(
            L.latLng(minLat, minLng),
            L.latLng(maxLat, maxLng)
          );
          map.fitBounds(dataBounds, { 
            padding: [50, 50], 
            maxZoom: 9,
            animate: false 
          });
          log("Map fitted to data bounds");
        } catch (boundsErr) {
          log("Bounds error, using fixed center");
          map.setView([lat, lng], zoom);
        }

      } catch (err) {
        console.error("Update error:", err.message);
        log("ERROR: " + err.message);
      }
    };

    // Initial view
    map.setView([33.749, -84.388], 9);
    log("Ready");

  </script>
</body>
</html>
`;

export default function HeatmapView({
  points,
  centerLat = 33.749,
  centerLng = -84.388,
  zoom = 9,
}: HeatmapViewProps) {
  const webviewRef = useRef<WebView>(null);

  useEffect(() => {
    if (!points || points.length === 0) {
      console.log("[HeatmapView] No points to display");
      return;
    }

    const js = `
      window.updateHeatmap(
        ${centerLat},
        ${centerLng},
        ${zoom},
        ${JSON.stringify(points)}
      );
      true;
    `;
    
    webviewRef.current?.injectJavaScript(js);
  }, [points, centerLat, centerLng, zoom]);

  return (
    <WebView
      ref={webviewRef}
      source={{ html: MAP_HTML }}
      style={StyleSheet.absoluteFill}
      originWhitelist={["*"]}
      javaScriptEnabled
      domStorageEnabled
      scrollEnabled={true}
      overScrollMode="never"
      mixedContentMode="always"
    />
  );
}