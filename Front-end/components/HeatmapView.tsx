import { useRef, useEffect, useCallback, useMemo, useState } from "react";
import { PanResponder, Platform, StyleSheet, View, TouchableOpacity, Text } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import type { MapRaster } from "../lib/forecast-map";

export type MapBounds = [[number, number], [number, number]];
export const ATLANTA_BOUNDS: MapBounds = [[-84.80, 33.45], [-83.95, 34.05]];
interface MapLocation { latitude: number; longitude: number }
interface HeatmapViewProps {
  raster: MapRaster | null;
  hoursAhead: number;
  maxHours: number;
  onHoursAheadChange: (hours: number) => void;
  userLocation: MapLocation | null;
  visualization?: "cells" | "heat";
  startBounds?: MapBounds;
  status: string;
  note: string;
  validLabel: string;
  colors?: string[];
  onRetry: () => void;
}

function buildMapHtml(startBounds: MapBounds): string {
  return `<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.css"/>
<style>*{margin:0;padding:0;box-sizing:border-box}html,body,#map{width:100%;height:100%}body{background:#f4f7f4}</style>
</head><body><div id="map"></div>
<script src="https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.js"></script><script>
const START_BOUNDS = ${JSON.stringify(startBounds)};
let map, ready=false, currentRaster=null, mode="heat", marker=null;
function post(message) {
  const text=JSON.stringify(message);
  if(window.ReactNativeWebView) window.ReactNativeWebView.postMessage(text);
  else window.parent.postMessage(text,"*");
}
function setMode(next) {
  mode=next==="cells"?"cells":"heat";
  if(ready && map.getLayer("risk")) map.setPaintProperty("risk","raster-resampling",mode==="cells"?"nearest":"linear");
}
function setRaster(raster) {
  currentRaster=raster;
  if(!ready) return;
  if(map.getLayer("risk")) map.removeLayer("risk");
  if(map.getSource("risk")) map.removeSource("risk");
  if(!raster) return;
  const [w,s,e,n]=raster.bounds;
  map.addSource("risk",{type:"image",url:raster.image,coordinates:[[w,n],[e,n],[e,s],[w,s]]});
  map.addLayer({id:"risk",type:"raster",source:"risk",paint:{
    "raster-opacity":0.65,"raster-fade-duration":0,
    "raster-resampling":mode==="cells"?"nearest":"linear"
  }});
}
function setLocation(location) {
  if(marker) { marker.remove(); marker=null; }
  if(location && map) marker=new maplibregl.Marker({color:"#2d6a4f"}).setLngLat([location.longitude,location.latitude]).addTo(map);
}
function command(message) {
  if(message.type==="raster") setRaster(message.raster);
  if(message.type==="location") setLocation(message.location);
  if(message.type==="mode") setMode(message.mode);
  if(message.type==="zoom-in" && map) map.zoomIn();
  if(message.type==="zoom-out" && map) map.zoomOut();
}
window.mapCommand=command;
window.addEventListener("message",event=>{if(event.source===window.parent && event.data) command(event.data)});
try {
  map=new maplibregl.Map({container:"map",style:"https://tiles.openfreemap.org/styles/positron",
    bounds:START_BOUNDS,minZoom:5,maxZoom:15,dragRotate:false,pitchWithRotate:false,touchPitch:false,attributionControl:{compact:true}});
  map.touchZoomRotate.disableRotation();
  map.on("load",()=>{map.resize();map.fitBounds(START_BOUNDS,{animate:false});ready=true;setRaster(currentRaster);post({type:"ready"})});
  map.on("error",event=>{if(event.error) post({type:"error",message:"Map layer could not load. Tap to retry."})});
} catch(error) {post({type:"error",message:"Map could not initialize. Tap to retry."})}
</script></body></html>`;
}

export default function HeatmapView({raster,hoursAhead,maxHours,onHoursAheadChange,userLocation,
  visualization="heat",startBounds=ATLANTA_BOUNDS,status,note,validLabel,colors,onRetry}: HeatmapViewProps) {
  const [mapHtml] = useState(() => buildMapHtml(startBounds));
  const webviewRef = useRef<WebView<object>>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const ready = useRef(false);
  const [timelineWidth,setTimelineWidth] = useState(0);
  const [mode,setMode] = useState(visualization);
  const [mapError,setMapError] = useState("");
  const latest = useRef({raster,userLocation,mode});
  useEffect(() => { latest.current={raster,userLocation,mode}; });
  const send = useCallback((message: object) => {
    iframeRef.current?.contentWindow?.postMessage(message,"*");
    webviewRef.current?.injectJavaScript(`window.mapCommand(${JSON.stringify(message)});true;`);
  },[]);
  const receive = useCallback((text: string) => {
    try {
      const message=JSON.parse(text);
      if(message.type==="ready") {
        ready.current=true;
        setMapError("");
        const current=latest.current;
        send({type:"raster",raster:current.raster});
        send({type:"location",location:current.userLocation});
        send({type:"mode",mode:current.mode});
      } else if(message.type==="error") setMapError(message.message);
    } catch { /* Ignore messages unrelated to the map bridge. */ }
  },[send]);
  useEffect(() => {
    if(Platform.OS!=="web") return;
    const handler=(event: MessageEvent) => {
      if(event.source===iframeRef.current?.contentWindow && typeof event.data==="string") receive(event.data);
    };
    window.addEventListener("message",handler);
    return () => window.removeEventListener("message",handler);
  },[receive]);
  useEffect(() => { if(ready.current) send({type:"raster",raster}); },[raster,send]);
  useEffect(() => { if(ready.current) send({type:"location",location:userLocation}); },[userLocation,send]);
  useEffect(() => { if(ready.current) send({type:"mode",mode}); },[mode,send]);
  const selectPosition=useCallback((x:number)=>{
    if(timelineWidth>0 && maxHours>0) onHoursAheadChange(Math.round(Math.max(0,Math.min(x/timelineWidth,1))*maxHours));
  },[timelineWidth,maxHours,onHoursAheadChange]);
  const pan=useMemo(()=>PanResponder.create({onStartShouldSetPanResponder:()=>true,onMoveShouldSetPanResponder:()=>true,
    onPanResponderGrant:event=>selectPosition(event.nativeEvent.locationX),
    onPanResponderMove:event=>selectPosition(event.nativeEvent.locationX)}),[selectPosition]);
  const changeHour=(delta:number)=>onHoursAheadChange(Math.min(maxHours,Math.max(0,hoursAhead+delta)));
  const progress=maxHours>0?hoursAhead/maxHours*100:0;
  const ticks=Array.from(new Set([0,Math.round(maxHours/4),Math.round(maxHours/2),Math.round(maxHours*3/4),maxHours]));
  const reload=()=>{setMapError("");ready.current=false;if(Platform.OS==="web"){if(iframeRef.current) iframeRef.current.srcdoc=mapHtml;}else webviewRef.current?.reload();onRetry();};
  return <View style={StyleSheet.absoluteFill}>
    {Platform.OS==="web" ? <iframe ref={iframeRef} title="Forecast map" srcDoc={mapHtml} sandbox="allow-scripts"
      style={{width:"100%",height:"100%",border:0,display:"block"}}/> :
      <WebView<object> ref={webviewRef} source={{html:mapHtml}} style={StyleSheet.absoluteFill}
        originWhitelist={["*"]} javaScriptEnabled domStorageEnabled scrollEnabled={false} overScrollMode="never"
        onMessage={(event:WebViewMessageEvent)=>receive(event.nativeEvent.data)} />}
    <View style={styles.zoomControls} pointerEvents="box-none">
      <TouchableOpacity style={styles.zoomBtn} onPress={()=>send({type:"zoom-in"})} accessibilityRole="button" accessibilityLabel="Zoom in"><Text style={styles.zoomText}>+</Text></TouchableOpacity>
      <TouchableOpacity style={styles.zoomBtn} onPress={()=>send({type:"zoom-out"})} accessibilityRole="button" accessibilityLabel="Zoom out"><Text style={styles.zoomText}>−</Text></TouchableOpacity>
    </View>
    <View style={styles.modeToggle}>{(["heat","cells"] as const).map(option=><TouchableOpacity key={option}
      style={[styles.modeBtn,mode===option&&styles.modeBtnActive]} onPress={()=>setMode(option)}
      accessibilityRole="button" accessibilityState={{selected:mode===option}}>
      <Text style={[styles.modeText,mode===option&&styles.modeTextActive]}>{option==="heat"?"Heat":"Grid"}</Text>
    </TouchableOpacity>)}</View>
    <View style={styles.mapLegend}>
      {userLocation&&<View style={styles.locationLegendRow}><View style={styles.locationLegendDot}/><Text style={styles.mapLegendText}>Your location</Text></View>}
      <View style={styles.intensityLegend}><Text style={styles.mapLegendTitle}>General risk score</Text>
        <View style={styles.intensityGradient}>{(colors??["#2547a8","#20b9c7","#69cf71","#f4d84a","#e53935"]).map(color=><View key={color} style={[styles.intensityColor,{backgroundColor:color}]}/>)}</View>
        <View style={styles.intensityLabels}><Text style={styles.mapLegendCaption}>0 · Lower</Text><Text style={styles.mapLegendCaption}>100 · Higher</Text></View>
      </View>
    </View>
    <View style={styles.timeline}>
      <View style={styles.timelineHeading}><Text style={styles.timelineTitle}>Forecast timeline</Text>
        <Text style={styles.timelineValue}>{hoursAhead===0?"Now":`+${hoursAhead}h`}</Text></View>
      {!!validLabel&&<Text style={styles.timelineNote}>{validLabel}</Text>}
      <View {...pan.panHandlers} style={styles.timelineTrackTouchArea} onLayout={event=>setTimelineWidth(event.nativeEvent.layout.width)}
        accessible focusable accessibilityRole="adjustable" accessibilityLabel="Forecast timeline"
        accessibilityValue={{min:0,max:maxHours,now:hoursAhead,text:validLabel}}
        aria-valuemin={0} aria-valuemax={maxHours} aria-valuenow={hoursAhead} aria-valuetext={validLabel}
        accessibilityActions={[{name:"decrement",label:"Move back one hour"},{name:"increment",label:"Advance one hour"}]}
        onAccessibilityAction={event=>changeHour(event.nativeEvent.actionName==="increment"?1:-1)}
        {...(Platform.OS==="web"?{tabIndex:0,onKeyDown:(event: import("react-native").KeyDownEvent)=>{
          const key=event.nativeEvent.key;
          if(key==="ArrowLeft"||key==="ArrowRight"){event.preventDefault();changeHour(key==="ArrowRight"?1:-1);}
        }}:{})}>
        <View style={styles.timelineTrack} pointerEvents="none"><View style={[styles.timelineFill,{width:`${progress}%`}]}/>
          {ticks.map(hour=><View key={hour} style={[styles.timelineMark,{left:`${maxHours?hour/maxHours*100:0}%`},hour<=hoursAhead&&styles.timelineMarkActive]}/>)}
          <View style={[styles.timelineThumb,{left:`${progress}%`}]}/></View>
      </View>
      <View style={styles.timelineLabels}>{ticks.map(hour=><Text key={hour} style={styles.timelineLabel}>{hour===0?"Now":`${hour}h`}</Text>)}</View>
      {!!note&&<Text style={styles.timelineNote}>{note}</Text>}
      {!!(status||mapError)&&<TouchableOpacity onPress={mapError?reload:onRetry} accessibilityRole="button" accessibilityLabel={mapError||status}>
        <Text style={styles.timelineNote}>{mapError||status}</Text></TouchableOpacity>}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  zoomControls: {
    position: "absolute",
    top: 145,
    right: 14,
    gap: 8,
  },
  zoomBtn: {
    width: 36,
    height: 36,
    borderRadius: 8,
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#dce5de",
    alignItems: "center",
    justifyContent: "center",
  },
  zoomText: {
    color: "#2d6a4f",
    fontSize: 20,
    fontWeight: "400",
    lineHeight: 22,
  },
  modeToggle: {
    position: "absolute",
    top: 55,
    right: 14,
    borderRadius: 8,
    overflow: "hidden",
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#dce5de",
  },
  modeBtn: {
    width: 52,
    paddingVertical: 7,
    alignItems: "center",
  },
  modeBtnActive: {
    backgroundColor: "#2d6a4f",
  },
  modeText: {
    color: "#34473b",
    fontSize: 12,
    fontWeight: "600",
  },
  modeTextActive: {
    color: "#ffffff",
  },
  mapLegend: {
    position: "absolute",
    top: 55,
    left: 14,
    width: 166,
    padding: 10,
    borderRadius: 12,
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#dce5de",
    gap: 8,
    elevation: 2,
  },
  locationLegendRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
  },
  locationLegendDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: "#2d6a4f",
    borderWidth: 2,
    borderColor: "#d8e3db",
  },
  mapLegendText: {
    color: "#34473b",
    fontSize: 11,
    fontWeight: "600",
  },
  intensityLegend: {
    gap: 4,
  },
  mapLegendTitle: {
    color: "#34473b",
    fontSize: 10,
    fontWeight: "700",
  },
  intensityGradient: {
    height: 8,
    flexDirection: "row",
    overflow: "hidden",
    borderRadius: 4,
  },
  intensityColor: {
    flex: 1,
  },
  intensityLabels: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  mapLegendCaption: {
    color: "#748178",
    fontSize: 9,
  },
  timeline: {
    position: "absolute",
    left: 14,
    right: 14,
    bottom: 14,
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 10,
    borderRadius: 14,
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#dce5de",
    gap: 3,
    elevation: 3,
  },
  timelineHeading: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  timelineTitle: {
    color: "#34473b",
    fontSize: 12,
    fontWeight: "700",
  },
  timelineValue: {
    color: "#2d6a4f",
    fontSize: 14,
    fontWeight: "800",
  },
  timelineNote: {
    color: "#748178",
    fontSize: 10,
  },
  timelineTrackTouchArea: {
    height: 30,
    justifyContent: "center",
    marginHorizontal: 4,
  },
  timelineTrack: {
    height: 4,
    borderRadius: 2,
    backgroundColor: "#e1e8e3",
    justifyContent: "center",
  },
  timelineFill: {
    position: "absolute",
    top: 0,
    bottom: 0,
    left: 0,
    borderRadius: 2,
    backgroundColor: "#2d6a4f",
  },
  timelineMark: {
    position: "absolute",
    top: -2,
    width: 2,
    height: 8,
    marginLeft: -1,
    backgroundColor: "#9bac9f",
  },
  timelineMarkActive: {
    backgroundColor: "#ffffff",
  },
  timelineThumb: {
    position: "absolute",
    top: -7,
    width: 18,
    height: 18,
    marginLeft: -9,
    borderRadius: 9,
    borderWidth: 3,
    borderColor: "#ffffff",
    backgroundColor: "#2d6a4f",
    shadowColor: "#203629",
    shadowOpacity: 0.2,
    shadowRadius: 3,
    shadowOffset: { width: 0, height: 1 },
  },
  timelineLabels: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  timelineLabel: {
    color: "#748178",
    fontSize: 10,
    fontWeight: "600",
  },
});
