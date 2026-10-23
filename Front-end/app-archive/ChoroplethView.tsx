import { useRef, useEffect, useState } from "react";
import { StyleSheet } from "react-native";
import { WebView } from "react-native-webview";
import { ZipcodeDataPoint, VariableMeta } from "../constants/socioeconomicData";

interface ChoroplethViewProps {
  data: ZipcodeDataPoint[];
  meta: VariableMeta;
  variableLabel: string;
}

const ZCTA_GEOJSON_URL = 
  "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/4/query" + 
  "?where=GEOID+LIKE+'303%25'" + 
  "&outFields=GEOID,NAME" +
  "&geometryPrecision=3" + 
  "&f=geojson&outSR=4326";

const MAP_HTML = `<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    html, body, #map { width:100%; height:100%; background:#0a0a14; }
    

    .leaflet-popup-content-wrapper {
      background: rgba(20, 20, 35, 0.9) !important;
      color: #ffffff !important;
      border-radius: 14px !important;
      border: 1px solid rgba(255, 255, 255, 0.15) !important;
      backdrop-filter: blur(8px);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6) !important;
      padding: 0 !important;
    }
    .leaflet-popup-tip {
      background: rgba(20, 20, 35, 0.9) !important;
      border: 1px solid rgba(255, 255, 255, 0.15) !important;
    }
    .leaflet-popup-content {
      margin: 12px 16px !important;
      line-height: 1.4 !important;
      min-width: 140px;
    }
    .info-header { font-size: 10px; text-transform: uppercase; color: rgba(255, 255, 255, 0.5); }
    .info-zip { font-size: 18px; font-weight: 700; margin-bottom: 4px; }
    .info-label { font-size: 12px; color: rgba(255, 255, 255, 0.8); }
    .info-value { font-size: 15px; font-weight: 600; color: #4fc3f7; }

    /* ── Legend Styles ── */
    /* ── Updated Legend Styles ── */
    .legend {
      position: absolute; 
      bottom: 30px; 
      right: 15px; 
      z-index: 1000;
      background: rgba(20, 20, 35, 0.9); 
      padding: 12px; 
      border-radius: 12px;
      color: white; 
      font-family: -apple-system, sans-serif;
      border: 1px solid rgba(255, 255, 255, 0.15);
      backdrop-filter: blur(8px);
      box-shadow: 0 4px 24px rgba(0, 0, 0, 0.5);
      min-width: 120px;
    }
    .legend-title { 
      font-size: 10px; 
      text-transform: uppercase; 
      color: rgba(255, 255, 255, 0.5); 
      margin-bottom: 8px; 
      font-weight: 600;
      letter-spacing: 0.5px;
    }
    .legend-row { 
      display: flex; 
      align-items: center; 
      gap: 10px; 
      margin-bottom: 6px; 
      font-size: 11px;
    }
    .legend-box { 
      width: 14px; 
      height: 14px; 
      border-radius: 3px; 
      border: 1px solid rgba(255,255,255,0.1);
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="diag-console">
    <div style="font-weight:bold; border-bottom:1px solid #333; margin-bottom:5px;">DIAGNOSTIC CHECKER</div>
    <div id="step-1">○ WebView Initialization...</div>
    <div id="step-2">○ Leaflet.js Loading...</div>
    <div id="step-3">○ Census API Fetch...</div>
    <div id="step-4">○ React Native Data Bridge...</div>
    <div id="step-5">○ Map Render...</div>
  </div>
  <div id="legend-container" class="legend"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const RAMPS = {
      red:    ['#fee5d9','#fcae91','#fb6a4a','#de2d26','#a50f15'],
      orange: ['#fff5eb','#fdd0a2','#fdae61','#f46d43','#d73027'],
      purple: ['#f2f0f7','#cbc9e2','#9e9ac8','#756bb1','#54278f'],
      green:  ['#edf8e9','#bae4b3','#74c476','#31a354','#006d2c'],
      blue:   ['#eff3ff','#bdd7e7','#6baed6','#3182bd','#08519c'],
      teal:   ['#e4f2f7','#a3d4e0','#62b7c9','#2199b2','#007c9b']
    };

    const diag = (id, text, status) => {
      const el = document.getElementById(id);
      if (el) {
        el.innerHTML = (status === 'ok' ? '● ' : status === 'err' ? '✖ ' : '○ ') + text;
        el.className = 'status-' + status;
      }
    };

    const map = L.map('map', { zoomControl: false, attributionControl: false });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png').addTo(map);
    map.setView([33.75, -84.39], 11);

    diag('step-1', 'WebView Ready', 'ok');
    diag('step-2', 'Leaflet.js Loaded', 'ok');

    let zctaGeoJSON = null;
    let geojsonLayer = null;
    let currentDataMap = {};

    window.updateChoropleth = (data, scheme, label, unit, hib) => {
      diag('step-4', 'Data Received (' + data.length + ' zips)', 'ok');
      window.currentSocioData = { data, scheme, label, unit, hib };
      renderMap();
    };

    function getColor(val, min, max, scheme, hib) {
      if (val === undefined || val === null) return 'rgba(255,255,255,0.08)';
      const ramp = RAMPS[scheme] || RAMPS.red;
      let pct = (val - min) / (max - min);
      if (hib) pct = 1 - pct; // Flip if higher is better
      const idx = Math.min(Math.floor(pct * ramp.length), ramp.length - 1);
      return ramp[idx];
    }

    function renderMap() {
      if (!window.currentSocioData || !zctaGeoJSON) return;
      
      const { data, scheme, label, unit, hib } = window.currentSocioData;
      if (geojsonLayer) map.removeLayer(geojsonLayer);

      const vals = data.map(d => d.value);
      const min = Math.min(...vals);
      const max = Math.max(...vals);
      currentDataMap = {};
      data.forEach(d => currentDataMap[d.zipcode] = d.value);

      geojsonLayer = L.geoJSON(zctaGeoJSON, {
        style: (f) => {
          const zip = f.properties.GEOID || f.properties.NAME;
          const val = currentDataMap[zip];
          return {
            fillColor: getColor(val, min, max, scheme, hib),
            weight: 1, color: 'white', fillOpacity: 0.7
          };
        },
        onEachFeature: (f, layer) => {
          const zip = f.properties.GEOID || f.properties.NAME;
          const val = currentDataMap[zip];
          
          layer.on('click', (e) => {
            const popupContent = \`
              <div class="info-box">
                <div class="info-zip">ZIP Code \${zip}</div>
                <div class="info-val">\${label}: \${val !== undefined ? val + unit : 'No Data'}</div>
              </div>
            \`;
            L.popup().setLatLng(e.latlng).setContent(popupContent).openOn(map);
          });
        }
      }).addTo(map);

      map.fitBounds(geojsonLayer.getBounds());
      diag('step-5', 'Map Rendered Successfully', 'ok');
      setTimeout(() => document.getElementById('diag-console').style.display = 'none', 3000);
    }

    fetch("${ZCTA_GEOJSON_URL}")
      .then(res => res.json())
      .then(json => {
        zctaGeoJSON = json;
        diag('step-3', 'Census Data Loaded', 'ok');
        renderMap();
      })
      .catch(err => diag('step-3', 'Census Error: ' + err.message, 'err'));

    window.onload = () => window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'ready' }));
  </script>
</body>
</html>`;

export default function ChoroplethView({ data, meta, variableLabel }: ChoroplethViewProps) {
  const webviewRef = useRef<WebView>(null);
  const [isWebViewReady, setIsWebViewReady] = useState(false);

  useEffect(() => {
    if (!isWebViewReady || !data || data.length === 0) return;

    const js = `
      window.updateChoropleth(
        ${JSON.stringify(data)},
        ${JSON.stringify(meta.colorScheme)},
        ${JSON.stringify(variableLabel)},
        ${JSON.stringify(meta.unit)},
        ${JSON.stringify(meta.higherIsBetter)}
      );
      true;
    `;
    webviewRef.current?.injectJavaScript(js);
  }, [data, meta, variableLabel, isWebViewReady]);

  return (
    <WebView
      ref={webviewRef}
      source={{ html: MAP_HTML }}
      style={StyleSheet.absoluteFill}
      javaScriptEnabled
      onMessage={(event) => {
        try {
          const msg = JSON.parse(event.nativeEvent.data);
          if (msg.type === 'ready') setIsWebViewReady(true);
          if (msg.type === 'log') console.log("[WebView Diagnostic]", msg.message);
        } catch (e) {
          if (event.nativeEvent.data === 'ready') setIsWebViewReady(true);
        }
      }}
    />
  );
}