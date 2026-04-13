import { useRef, useEffect } from "react";
import { StyleSheet } from "react-native";
import { WebView } from "react-native-webview";
import { CountyDataPoint, VariableMeta } from "../constants/socioeconomicData";

interface ChoroplethViewProps {
  data: CountyDataPoint[];
  meta: VariableMeta;
  variableLabel: string;
}

// ── Georgia county GeoJSON ─────────────────────────────────────────────────────
// Fetched inside the WebView from the US Census Bureau TIGER/Web service.
// Returns all 159 Georgia counties with GEOID (5-digit FIPS) and NAME fields.
// GEOID values match the `fips` field in CountyDataPoint (e.g. "13121").
const COUNTY_GEOJSON_URL =
  "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/" +
  "State_County/MapServer/11/query" +
  "?where=STATE%3D%2713%27&outFields=GEOID%2CNAME&f=geojson&outSR=4326";

// ── Inline HTML / JS for the WebView ──────────────────────────────────────────
const MAP_HTML = `<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    html, body, #map { width:100%; height:100%; background:#0a0a14; }
    .leaflet-control-attribution { display:none; }

    /* ── Loading overlay ── */
    #loading-overlay {
      position:absolute; inset:0;
      background:rgba(10,10,20,0.85);
      display:flex; flex-direction:column;
      align-items:center; justify-content:center; gap:14px;
      z-index:2000;
      font-family:-apple-system,BlinkMacSystemFont,sans-serif;
      color:rgba(255,255,255,0.65); font-size:13px;
    }
    .spinner {
      width:32px; height:32px;
      border:3px solid rgba(79,195,247,0.25);
      border-top-color:#4fc3f7;
      border-radius:50%;
      animation:spin 0.85s linear infinite;
    }
    @keyframes spin { to { transform:rotate(360deg); } }

    /* ── County tap info panel (top-right) ── */
    #info-panel {
      position:absolute; top:12px; right:12px;
      background:rgba(15,15,25,0.88);
      color:#fff; padding:10px 14px;
      border-radius:12px;
      border:1px solid rgba(255,255,255,0.13);
      font-family:-apple-system,BlinkMacSystemFont,sans-serif;
      font-size:12px; z-index:1000; min-width:148px;
      pointer-events:none;
    }

    /* ── Legend (bottom-right) ── */
    #legend-panel {
      position:absolute; bottom:12px; right:12px;
      background:rgba(15,15,25,0.88);
      color:#fff; padding:10px 14px;
      border-radius:12px;
      border:1px solid rgba(255,255,255,0.13);
      font-family:-apple-system,BlinkMacSystemFont,sans-serif;
      font-size:11px; z-index:1000;
      pointer-events:none; min-width:120px;
    }

    /* ── Leaflet tooltip override ── */
    .county-tip {
      background:rgba(15,15,25,0.95) !important;
      border:1px solid rgba(255,255,255,0.18) !important;
      border-radius:8px !important; color:#fff !important;
      padding:5px 10px !important; font-size:12px !important;
      box-shadow:0 4px 12px rgba(0,0,0,0.5) !important;
      pointer-events:none !important;
    }
    .county-tip::before { display:none !important; }
  </style>
</head>
<body>
  <div id="map"></div>

  <div id="loading-overlay">
    <div class="spinner"></div>
    <span>Loading county boundaries…</span>
  </div>

  <div id="info-panel">
    <div style="color:rgba(255,255,255,0.4);font-size:10px;letter-spacing:0.5px;text-transform:uppercase;">
      Tap a county
    </div>
  </div>

  <div id="legend-panel">
    <div id="legend-title" style="font-weight:600;color:rgba(255,255,255,0.85);margin-bottom:7px;font-size:11px;">—</div>
    <div id="legend-body"></div>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    // ── Map init ────────────────────────────────────────────────────────────────
    const map = L.map('map', {
      zoomControl: false, attributionControl: false,
      dragging: true, touchZoom: true,
      scrollWheelZoom: true, doubleClickZoom: true,
    });

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 18,
    }).addTo(map);

    map.setView([33.5, -84.0], 8);

    // ── State ───────────────────────────────────────────────────────────────────
    let countyGeoJSON = null;
    let geojsonLayer  = null;
    let pendingUpdate = null;  // queued if data arrives before GeoJSON loads
    let currentData   = {};    // fips → value
    let currentMeta   = {
      colorScheme: 'red', higherIsBetter: false,
      label: '', unit: '', min: 0, max: 1,
    };

    // ── Color ramps: [low, mid, high] hex ──────────────────────────────────────
    // Each ramp goes from "low concern" → "high concern".
    // For higherIsBetter metrics the ramp is reversed at render time.
    const RAMPS = {
      red:    ['#4575b4', '#fee090', '#d73027'],  // blue → yellow → red
      orange: ['#4575b4', '#fee090', '#f46d43'],  // blue → yellow → orange
      purple: ['#f0f0f0', '#9970ab', '#40004b'],  // light → purple
      green:  ['#d73027', '#fee090', '#1a9850'],  // red → yellow → green
      blue:   ['#ffffcc', '#41b6c4', '#0c2c84'],  // cream → teal → dark blue
    };

    function hexToRgb(hex) {
      return [
        parseInt(hex.slice(1,3), 16),
        parseInt(hex.slice(3,5), 16),
        parseInt(hex.slice(5,7), 16),
      ];
    }

    function lerpInt(a, b, t) { return Math.round(a + (b - a) * t); }

    /** Map t∈[0,1] through a 3-stop ramp */
    function rampColor(scheme, t) {
      const stops = (RAMPS[scheme] || RAMPS.red).map(hexToRgb);
      let rgb;
      if (t <= 0.5) {
        const s = t * 2;
        rgb = stops[0].map((v, i) => lerpInt(v, stops[1][i], s));
      } else {
        const s = (t - 0.5) * 2;
        rgb = stops[1].map((v, i) => lerpInt(v, stops[2][i], s));
      }
      return 'rgb(' + rgb.join(',') + ')';
    }

    /** Get fill color for a county by FIPS */
    function countyColor(fips) {
      const val = currentData[fips];
      if (val === undefined || val === null) return 'rgba(40,40,60,0.55)';
      const range = currentMeta.max - currentMeta.min;
      const raw   = range > 0 ? (val - currentMeta.min) / range : 0.5;
      // Flip so that "bad" is always the warm end of the ramp
      const t = currentMeta.higherIsBetter ? 1 - raw : raw;
      return rampColor(currentMeta.colorScheme, Math.max(0, Math.min(1, t)));
    }

    function countyStyle(feature) {
      const fips = feature.properties.GEOID || feature.properties.fips;
      return {
        fillColor:   countyColor(fips),
        fillOpacity: 0.78,
        color:       'rgba(255,255,255,0.2)',
        weight:      0.8,
      };
    }

    // ── County tap handler ──────────────────────────────────────────────────────
    function showCountyInfo(fips, name) {
      const val   = currentData[fips];
      const panel = document.getElementById('info-panel');
      if (!panel) return;

      const displayVal = (val !== undefined && val !== null)
        ? (currentMeta.unit === '$'
            ? '$' + Math.round(val).toLocaleString()
            : val.toFixed(val > 100 ? 0 : 1) + '\u202f' + currentMeta.unit)
        : 'No data';

      // Color the value to match the county fill
      const range = currentMeta.max - currentMeta.min;
      const raw   = range > 0 ? (val - currentMeta.min) / range : 0.5;
      const t     = currentMeta.higherIsBetter ? 1 - raw : raw;
      const valColor = (val !== undefined) ? rampColor(currentMeta.colorScheme, Math.max(0, Math.min(1, t))) : '#4fc3f7';

      panel.innerHTML =
        '<div style="font-weight:600;color:rgba(255,255,255,0.9);margin-bottom:3px;font-size:13px;">' + name + '</div>' +
        '<div style="font-size:22px;font-weight:700;color:' + valColor + ';line-height:1.15;">' + displayVal + '</div>' +
        '<div style="font-size:9px;color:rgba(255,255,255,0.35);margin-top:3px;text-transform:uppercase;letter-spacing:0.5px;">' +
          currentMeta.label +
        '</div>';
    }

    // ── Legend builder ──────────────────────────────────────────────────────────
    function buildLegend() {
      const titleEl = document.getElementById('legend-title');
      const bodyEl  = document.getElementById('legend-body');
      if (!titleEl || !bodyEl) return;
      titleEl.textContent = currentMeta.label;

      const STEPS = 5;
      let html = '';
      for (let i = STEPS; i >= 0; i--) {
        const t     = i / STEPS;
        const rawT  = currentMeta.higherIsBetter ? 1 - t : t;
        const color = rampColor(currentMeta.colorScheme, rawT);
        const val   = currentMeta.min + t * (currentMeta.max - currentMeta.min);
        const lbl   = currentMeta.unit === '$'
          ? '$' + Math.round(val).toLocaleString()
          : val.toFixed(val > 100 ? 0 : 1) + '\u202f' + currentMeta.unit;

        html +=
          '<div style="display:flex;align-items:center;gap:7px;margin-bottom:4px;">' +
            '<div style="width:11px;height:11px;border-radius:2px;background:' + color + ';flex-shrink:0;"></div>' +
            '<span style="color:rgba(255,255,255,0.62);font-size:10px;">' + lbl + '</span>' +
          '</div>';
      }
      bodyEl.innerHTML = html;
    }

    // ── Core update logic ───────────────────────────────────────────────────────
    function applyUpdate(dataArray, colorScheme, label, unit, higherIsBetter) {
      // Rebuild fips → value lookup and compute min/max
      currentData = {};
      let mn = Infinity, mx = -Infinity;
      for (const d of dataArray) {
        currentData[d.fips] = d.value;
        if (d.value < mn) mn = d.value;
        if (d.value > mx) mx = d.value;
      }
      currentMeta = { colorScheme, higherIsBetter, label, unit, min: mn, max: mx };

      buildLegend();

      if (geojsonLayer) {
        // Re-style in-place (fast — no re-creation of DOM elements)
        geojsonLayer.setStyle(countyStyle);
      } else if (countyGeoJSON) {
        geojsonLayer = L.geoJSON(countyGeoJSON, {
          style: countyStyle,
          onEachFeature(feature, layer) {
            const fips = feature.properties.GEOID || feature.properties.fips;
            const name = feature.properties.NAME  || fips;
            layer.on('click', () => showCountyInfo(fips, name));
            layer.bindTooltip(name, {
              className: 'county-tip',
              sticky: true,
              direction: 'top',
              offset: [0, -4],
            });
          },
        }).addTo(map);

        try {
          map.fitBounds(geojsonLayer.getBounds(), {
            padding: [30, 30], maxZoom: 9, animate: false,
          });
        } catch (_) {}
      }
    }

    // ── Public API — called from React Native via injectJavaScript ──────────────
    window.updateChoropleth = function(dataArray, colorScheme, label, unit, higherIsBetter) {
      if (!countyGeoJSON) {
        // GeoJSON hasn't arrived yet — queue the update
        pendingUpdate = [dataArray, colorScheme, label, unit, higherIsBetter];
        return;
      }
      applyUpdate(dataArray, colorScheme, label, unit, higherIsBetter);
    };

    // ── Fetch Georgia county boundaries ─────────────────────────────────────────
    fetch('${COUNTY_GEOJSON_URL}')
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(geojson => {
        countyGeoJSON = geojson;
        document.getElementById('loading-overlay').style.display = 'none';

        if (pendingUpdate) {
          applyUpdate(...pendingUpdate);
          pendingUpdate = null;
        }
      })
      .catch(err => {
        const overlay = document.getElementById('loading-overlay');
        overlay.innerHTML =
          '<div style="color:#ff6b6b;font-size:13px;text-align:center;padding:0 28px;">' +
            '⚠ Could not load county boundaries' +
            '<br><span style="font-size:10px;color:rgba(255,255,255,0.35);display:block;margin-top:6px;">' +
              err.message +
            '</span>' +
          '</div>';
      });
  </script>
</body>
</html>`;

// ── Component ─────────────────────────────────────────────────────────────────

export default function ChoroplethView({ data, meta, variableLabel }: ChoroplethViewProps) {
  const webviewRef = useRef<WebView>(null);

  useEffect(() => {
    if (!data || data.length === 0) return;

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
  }, [data, meta, variableLabel]);

  return (
    <WebView
      ref={webviewRef}
      source={{ html: MAP_HTML }}
      style={StyleSheet.absoluteFill}
      originWhitelist={["*"]}
      javaScriptEnabled
      domStorageEnabled
      mixedContentMode="always"
      overScrollMode="never"
      scrollEnabled={false}
    />
  );
}
