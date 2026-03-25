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
    /* Hide Leaflet attribution to keep UI clean */
    .leaflet-control-attribution { display: none; }
  </style>
</head>
<body>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
  <script>
    const map = L.map("map", {
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

    let heatLayer = L.heatLayer([], {
      radius: 35,
      blur: 20,
      maxZoom: 9,
      max: 1.0,
      minOpacity: 0.5,
      gradient: {
        0.0:  "#0000ff",
        0.25: "#00e5ff",
        0.5:  "#69ff47",
        0.75: "#ffa500",
        1.0:  "#ff0000",
      },
    }).addTo(map);

    // Called from React Native whenever data or view changes
    window.updateHeatmap = function(lat, lng, zoom, points) {
      map.setView([lat, lng], zoom);
      heatLayer.setLatLngs(points);
    };
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

  // Push updated data into the WebView whenever props change
  useEffect(() => {
    const js = `
      if (window.updateHeatmap) {
        window.updateHeatmap(
          ${centerLat},
          ${centerLng},
          ${zoom},
          ${JSON.stringify(points)}
        );
      }
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
      // Allow loading CDN assets
      mixedContentMode="always"
      onLoad={() => {
        // Push initial data once the page is ready
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
      }}
    />
  );
}
