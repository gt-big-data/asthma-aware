import { useEffect, useRef, useState } from "react";
import { Modal, Platform, Text, TextInput, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { WebView } from "react-native-webview";
import { parseCoordinates, validCoordinates, type Coordinates } from "../../lib/logging";
import { Button, colors, Notice, styles } from "./ui";

function mapHtml(point: Coordinates | null) {
  const latitude = point?.latitude ?? 33.749;
  const longitude = point?.longitude ?? -84.388;
  return `<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>html,body,#map{height:100%;width:100%;margin:0;background:#121c2c}</style></head><body><div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
  const map=L.map('map').setView([${latitude},${longitude}],12);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
  let marker=${point ? `L.marker([${latitude},${longitude}]).addTo(map)` : "null"};
  window.setSelectedPoint=function(lat,lon){
    if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180)return;
    if(marker)marker.setLatLng([lat,lon]);else marker=L.marker([lat,lon]).addTo(map);
    map.setView([lat,lon],map.getZoom());
  };
  window.addEventListener('message',function(e){
    if(e.source===window.parent&&e.data&&e.data.type==='set-symptom-location')window.setSelectedPoint(e.data.latitude,e.data.longitude);
  });
  map.on('click',function(e){
    if(marker)marker.setLatLng(e.latlng);else marker=L.marker(e.latlng).addTo(map);
    const msg={type:'symptom-location',latitude:e.latlng.lat,longitude:e.latlng.lng};
    if(window.ReactNativeWebView)window.ReactNativeWebView.postMessage(JSON.stringify(msg));
    else window.parent.postMessage(msg,'*');
  });</script></body></html>`;
}

export function LocationPicker({ initial, onSelect, onCancel }: {
  initial: Coordinates | null; onSelect: (point: Coordinates) => void; onCancel: () => void;
}) {
  const [html] = useState(() => mapHtml(initial));
  const [point, setPoint] = useState(initial);
  const [latitude, setLatitude] = useState(initial?.latitude.toString() ?? "");
  const [longitude, setLongitude] = useState(initial?.longitude.toString() ?? "");
  const frame = useRef<HTMLIFrameElement | null>(null);
  const mapRef = useRef<WebView<object>>(null);
  function receive(data: unknown) {
    if (!data || typeof data !== "object") return;
    const value = data as Record<string, unknown>;
    if (value.type !== "symptom-location" || typeof value.latitude !== "number" || typeof value.longitude !== "number") return;
    const next = { latitude: value.latitude, longitude: value.longitude };
    if (!validCoordinates(next)) return;
    setPoint(next); setLatitude(next.latitude.toFixed(6)); setLongitude(next.longitude.toFixed(6));
  }
  useEffect(() => {
    if (Platform.OS !== "web") return;
    function listener(event: MessageEvent) { if (event.source === frame.current?.contentWindow) receive(event.data); }
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  }, []);
  function edit(nextLatitude: string, nextLongitude: string) {
    setLatitude(nextLatitude); setLongitude(nextLongitude);
    const next = parseCoordinates(nextLatitude, nextLongitude);
    setPoint(next);
    if (next) {
      if (Platform.OS === "web") frame.current?.contentWindow?.postMessage({ type: "set-symptom-location", ...next }, "*");
      else mapRef.current?.injectJavaScript(`window.setSelectedPoint&&window.setSelectedPoint(${next.latitude},${next.longitude});true;`);
    }
  }
  return <Modal visible animationType="slide" onRequestClose={onCancel}>
    <SafeAreaView style={styles.screen}>
      <View style={{ padding: 18, gap: 12 }}>
        <Text style={styles.heading}>Choose a representative location</Text>
        <Notice>Tap the place that best represents where you were that day.</Notice>
      </View>
      <View style={{ flex: 1, minHeight: 180 }}>
        {Platform.OS === "web" ? <iframe ref={frame} srcDoc={html} sandbox="allow-scripts" title="Choose symptom location"
          style={{ width: "100%", height: "100%", border: 0 }} /> : <WebView<object> ref={mapRef} source={{ html }} originWhitelist={["*"]} onMessage={(event) => {
          try { receive(JSON.parse(event.nativeEvent.data)); } catch { /* Ignore malformed map events. */ }
        }} />}
      </View>
      <View style={{ padding: 18, gap: 12 }}>
        <Notice>You can also enter latitude and longitude if the map is unavailable.</Notice>
        <View style={styles.row}>
          <TextInput accessibilityLabel="Latitude" placeholder="Latitude" placeholderTextColor={colors.muted} value={latitude}
            onChangeText={(value) => edit(value, longitude)} keyboardType="numbers-and-punctuation" style={[styles.input, { flex: 1 }]} />
          <TextInput accessibilityLabel="Longitude" placeholder="Longitude" placeholderTextColor={colors.muted} value={longitude}
            onChangeText={(value) => edit(latitude, value)} keyboardType="numbers-and-punctuation" style={[styles.input, { flex: 1 }]} />
        </View>
        <Button title="Use this location" disabled={!point} onPress={() => { if (point) onSelect(point); }} />
        <Button secondary title="Cancel" onPress={onCancel} />
      </View>
    </SafeAreaView>
  </Modal>;
}
