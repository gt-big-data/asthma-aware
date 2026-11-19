import { useRef, useEffect, useCallback, useMemo, useState } from "react";
import { PanResponder, Platform, StyleSheet, View, TouchableOpacity, Text, ScrollView } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { GENERAL_RISK_LAYER, createCellGrid, type MapLayer, type MapLayerId, type MapRaster } from "../lib/forecast-map";

export type MapBounds = [[number, number], [number, number]];
export const ATLANTA_BOUNDS: MapBounds = [[-84.80, 33.45], [-83.95, 34.05]];
interface MapLocation { latitude: number; longitude: number }
interface HeatmapViewProps {
  layer?: MapLayer;
  layers?: MapLayer[];
  onLayerChange?: (layer: MapLayerId) => void;
  raster: MapRaster | null;
  hoursAhead: number;
  maxHours: number;
  onHoursAheadChange: (hours: number) => void;
  onRasterShown?: (key: string) => void;
  userLocation: MapLocation | null;
  visualization?: "cells" | "heat";
  gridStepDegrees?: number;
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
let map, ready=false, currentRaster=null, mode="heat", marker=null, gridVisible=false;
const riskCanvas=document.createElement("canvas"), riskContext=riskCanvas.getContext("2d");
const previousCanvas=document.createElement("canvas"), previousContext=previousCanvas.getContext("2d");
let rasterVersion=0, animation=0, hasImage=false, drawnBounds="";
function updateGridVisibility() {
  if(ready && map.getLayer("cell-lines")) map.setLayoutProperty("cell-lines","visibility",gridVisible && currentRaster?"visible":"none");
}
function setGrid(message) {
  gridVisible=!!message.visible;
  if(!ready) return;
  if(message.data) {
    if(map.getSource("cell-grid")) map.getSource("cell-grid").setData(message.data);
    else map.addSource("cell-grid",{type:"geojson",data:message.data});
    if(!map.getLayer("cell-lines")) map.addLayer({id:"cell-lines",type:"line",source:"cell-grid",minzoom:8,
      paint:{"line-color":"#243e32","line-width":0.6,
        "line-opacity":["interpolate",["linear"],["zoom"],8,0,9,0.12,11,0.24,15,0.3]}});
  }
  updateGridVisibility();
}
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
  if(ready && map.getSource("risk") && raster && currentRaster && raster.key===currentRaster.key && raster.image===currentRaster.image) return;
  currentRaster=raster;
  if(!ready) return;
  const version=++rasterVersion;
  if(!raster) {
    cancelAnimationFrame(animation);
    if(map.getSource("risk")) map.getSource("risk").pause();
    if(map.getLayer("risk")) map.setLayoutProperty("risk","visibility","none");
    hasImage=false;updateGridVisibility();return;
  }
  // Decode offscreen while the existing texture remains visible.
  const image=new Image();
  image.onload=()=>{
    if(version!==rasterVersion) return;
    cancelAnimationFrame(animation);
    const bounds=JSON.stringify(raster.bounds);
    const exact=raster.transition==="replace";
    const blend=!exact && hasImage && drawnBounds===bounds && riskCanvas.width===image.width && riskCanvas.height===image.height;
    if(blend) {
      previousCanvas.width=riskCanvas.width;previousCanvas.height=riskCanvas.height;
      previousContext.drawImage(riskCanvas,0,0);
    }
    if(riskCanvas.width!==image.width || riskCanvas.height!==image.height) {
      riskCanvas.width=image.width;riskCanvas.height=image.height;
    }
    const [w,s,e,n]=raster.bounds, coordinates=[[w,n],[e,n],[e,s],[w,s]];
    if(!map.getSource("risk")) {
      map.addSource("risk",{type:"canvas",canvas:riskCanvas,coordinates,animate:false});
      map.addLayer({id:"risk",type:"raster",source:"risk",paint:{
        "raster-opacity":0.65,"raster-fade-duration":0,
        "raster-resampling":mode==="cells"?"nearest":"linear"
      }},map.getLayer("cell-lines")?"cell-lines":undefined);
    } else map.getSource("risk").setCoordinates(coordinates);
    map.setLayoutProperty("risk","visibility","visible");
    const source=map.getSource("risk");
    source.play();
    const duration=blend && !window.matchMedia("(prefers-reduced-motion: reduce)").matches?140:0;
    const start=performance.now();
    function draw(now) {
      if(version!==rasterVersion) {source.pause();return;}
      const fraction=duration?Math.min(1,(now-start)/duration):1;
      const weight=fraction*fraction*(3-2*fraction);
      riskContext.clearRect(0,0,riskCanvas.width,riskCanvas.height);
      riskContext.globalCompositeOperation="source-over";
      if(blend && fraction<1) {
        riskContext.globalAlpha=1-weight;riskContext.drawImage(previousCanvas,0,0);
        riskContext.globalCompositeOperation="lighter";
        riskContext.globalAlpha=weight;riskContext.drawImage(image,0,0);
      } else {riskContext.globalAlpha=1;riskContext.drawImage(image,0,0);}
      riskContext.globalAlpha=1;riskContext.globalCompositeOperation="source-over";
      map.triggerRepaint();
      if(fraction<1) animation=requestAnimationFrame(draw);
      else if(exact) {
        // A physical concentration frame must be complete, never a blend of
        // hours. Acknowledge only after MapLibre renders the final texture.
        hasImage=true;drawnBounds=bounds;
        map.once("render",()=>{
          if(version!==rasterVersion) return;
          source.pause();post({type:"frame-shown",key:raster.key});
        });
        map.triggerRepaint();
      } else {source.pause();hasImage=true;drawnBounds=bounds;post({type:"frame-shown",key:raster.key});}
    }
    draw(start);
    updateGridVisibility();
  };
  image.onerror=()=>{if(version===rasterVersion) post({type:"error",message:"Risk image could not load. Tap to retry."})};
  image.src=raster.image;
}
function setLocation(location) {
  if(marker) { marker.remove(); marker=null; }
  if(location && map) marker=new maplibregl.Marker({color:"#2d6a4f"}).setLngLat([location.longitude,location.latitude]).addTo(map);
}
function command(message) {
  if(message.type==="raster") setRaster(message.raster);
  if(message.type==="location") setLocation(message.location);
  if(message.type==="mode") setMode(message.mode);
  if(message.type==="grid") setGrid(message);
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

export default function HeatmapView({raster,hoursAhead,maxHours,onHoursAheadChange,onRasterShown,userLocation,
  layer=GENERAL_RISK_LAYER,layers=[GENERAL_RISK_LAYER],onLayerChange,visualization="heat",gridStepDegrees=0.01,startBounds=ATLANTA_BOUNDS,status,note,validLabel,colors,onRetry}: HeatmapViewProps) {
  const [layerMenu, setLayerMenu] = useState(false);
  const [mapHtml] = useState(() => buildMapHtml(startBounds));
  const webviewRef = useRef<WebView<object>>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const ready = useRef(false);
  const [timelineWidth,setTimelineWidth] = useState(0);
  const [mode,setMode] = useState(visualization);
  const [showCellLines,setShowCellLines] = useState(false);
  const rasterBounds = raster?.bounds;
  const cellGrid = useMemo(() => rasterBounds ? createCellGrid(rasterBounds,gridStepDegrees) : null,
    [rasterBounds,gridStepDegrees]);
  const [mapError,setMapError] = useState("");
  const latest = useRef({raster,userLocation,mode,showCellLines,cellGrid,onRasterShown,layerId:layer.id});
  useEffect(() => { latest.current={raster,userLocation,mode,showCellLines,cellGrid,onRasterShown,layerId:layer.id}; });
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
        send({type:"grid",visible:current.showCellLines,data:current.cellGrid});
      } else if(message.type==="frame-shown") {
        if(latest.current.layerId!=="risk") setMapError("");
        latest.current.onRasterShown?.(message.key);
      }
      else if(message.type==="error") setMapError(message.message);
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
  useEffect(() => { if(ready.current) send({type:"grid",visible:showCellLines,data:cellGrid}); },[showCellLines,cellGrid,send]);
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
      <Text style={[styles.modeText,mode===option&&styles.modeTextActive]}>{option==="heat"?"Heat":"Cells"}</Text>
    </TouchableOpacity>)}
      <TouchableOpacity style={[styles.cellLineBtn,showCellLines&&styles.modeBtnActive]} onPress={()=>setShowCellLines(value=>!value)}
        accessibilityRole="switch" accessibilityLabel="Show forecast cell lines" accessibilityState={{checked:showCellLines}}>
        <Text style={[styles.modeText,showCellLines&&styles.modeTextActive]}>Cell lines</Text>
        <Text style={[styles.cellLineCaption,showCellLines&&styles.modeTextActive]}>{gridStepDegrees}° · {showCellLines?"On":"Off"}</Text>
      </TouchableOpacity>
    </View>
    <View style={styles.mapLegend}>
      <TouchableOpacity onPress={() => setLayerMenu(value => !value)} accessibilityRole="button"
        accessibilityLabel="Choose map layer" accessibilityState={{expanded: layerMenu}} style={styles.layerTrigger}>
        <Text style={styles.mapLegendTitle}>{layer.label} ▾</Text>
        <Text style={styles.mapLegendCaption}>Map layer</Text>
      </TouchableOpacity>
      {layerMenu && <ScrollView style={styles.layerMenu} accessibilityRole="radiogroup" accessibilityLabel="Map layers">
        {layers.map(option => <TouchableOpacity key={option.id} accessibilityRole="radio"
          accessibilityLabel={option.label} accessibilityState={{checked: option.id === layer.id}}
          style={[styles.layerOption, option.id === layer.id && styles.layerSelected]}
          onPress={() => { onLayerChange?.(option.id); setLayerMenu(false); }}>
          <Text style={styles.mapLegendText}>{option.label}</Text>
          <Text style={styles.mapLegendCaption}>{option.unit}</Text>
        </TouchableOpacity>)}
      </ScrollView>}
      {userLocation&&<View style={styles.locationLegendRow}><View style={styles.locationLegendDot}/><Text style={styles.mapLegendText}>Your location</Text></View>}
      <View style={styles.intensityLegend}><Text style={styles.mapLegendTitle}>{layer.unit}</Text>
        <View style={styles.intensityGradient}>{(colors??layer.colors).map(color=><View key={color} style={[styles.intensityColor,{backgroundColor:color}]}/>)}</View>
        <View style={styles.intensityLabels}><Text style={styles.mapLegendCaption}>{(layer.id === "risk" || layer.id === "personal") ? "0 · Lower" : `≤${layer.minimum}`}</Text><Text style={styles.mapLegendCaption}>{(layer.id === "risk" || layer.id === "personal") ? "100 · Higher" : `≥${layer.maximum}`}</Text></View>
      </View>
    </View>
    <View style={styles.timeline}>
      <View style={styles.timelineHeading}><Text style={styles.timelineTitle}>Forecast timeline</Text>
        <Text style={styles.timelineValue}>{hoursAhead===0?"Now":`+${hoursAhead}h`}</Text></View>
      {!!validLabel&&<Text style={[styles.timelineNote,styles.timelineTimestamp]} numberOfLines={1}
        ellipsizeMode="tail" accessibilityLabel={validLabel}>{validLabel}</Text>}
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
  layerTrigger: { gap: 3, paddingBottom: 5, borderBottomWidth: 1, borderBottomColor: "#e1e8e3" },
  layerMenu: { maxHeight: 230 },
  layerOption: { paddingVertical: 8, paddingHorizontal: 6, borderRadius: 6, gap: 2 },
  layerSelected: { backgroundColor: "#e7f2e9" },
  zoomControls: {
    position: "absolute",
    top: 174,
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
    width: 78,
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
  cellLineBtn: {
    width: 78,
    paddingVertical: 8,
    alignItems: "center",
    borderTopWidth: 1,
    borderTopColor: "#dce5de",
    gap: 2,
  },
  cellLineCaption: {
    color: "#748178",
    fontSize: 9,
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
  timelineTimestamp: {
    height: 14,
    lineHeight: 14,
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
