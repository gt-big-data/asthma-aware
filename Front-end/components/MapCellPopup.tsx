import LoadingFeedback from "./LoadingFeedback";
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { MapCell, MapLayer } from "../lib/forecast-map";

export default function MapCellPopup({ latitude, longitude, data, layer, error, busy, onClose, onRetry, onUse }: {
  latitude: number; longitude: number; data: MapCell | null; layer: MapLayer; error: string; busy: boolean;
  onClose: () => void; onRetry: () => void; onUse: () => void;
}) {
  return <View style={styles.card} accessibilityLabel="Selected forecast cell">
    <View style={styles.heading}><Text style={styles.title}>Selected cell</Text>
      <TouchableOpacity onPress={onClose} disabled={busy} accessibilityRole="button" accessibilityLabel="Close cell details"><Text style={styles.close}>×</Text></TouchableOpacity></View>
    <ScrollView contentContainerStyle={styles.content}>
      <Text style={styles.small}>{latitude.toFixed(5)}, {longitude.toFixed(5)} · 0.01°</Text>
      <View style={styles.reading}>
      {data ? <>
        <Text style={styles.small}>{new Date(data.valid_at).toLocaleString("en-US",{timeZone:"America/New_York",month:"short",day:"numeric",hour:"numeric",minute:"2-digit",timeZoneName:"short"})}</Text>
        <Text style={styles.score}>{data.risk_score === null ? "No risk data" : `${data.risk_score} / 100`}</Text>
        <Text style={styles.label}>{data.risk_kind === "personal" ? "Personalized asthma risk" : "General asthma risk"}</Text>
        {layer.id !== "risk" && layer.id !== "personal" && <Text style={styles.value}>{layer.label}: {data.layer_value === null ? "No data" : `${data.layer_value.toFixed(2)} ${data.layer_unit}`}</Text>}
        {!data.in_georgia && <Text style={styles.small}>Dashboard locations are restricted to Georgia.</Text>}
      </> : !error && <LoadingFeedback label="Loading selected forecast hour…" skeleton />}
      </View>
      {!!error && <TouchableOpacity onPress={onRetry} accessibilityRole="button" accessibilityLabel="Retry cell details"><Text style={styles.error}>{error}</Text></TouchableOpacity>}
      <TouchableOpacity disabled={busy || !data?.in_georgia} onPress={onUse} accessibilityRole="button" accessibilityLabel="Use selected cell for dashboard"
        style={[styles.useButton,(busy || !data?.in_georgia)&&styles.disabled]}>
        <View style={styles.buttonContent}>{busy&&<LoadingFeedback label="Selecting dashboard location" compact color="#fff"/>}
          <Text style={styles.useText}>{busy ? "Selecting…" : "Use for dashboard"}</Text></View>
      </TouchableOpacity>
    </ScrollView>
  </View>;
}
const styles=StyleSheet.create({
  reading:{minHeight:104,gap:7},buttonContent:{flexDirection:"row",alignItems:"center",gap:8},
  card:{position:"absolute",left:14,bottom:180,width:265,maxWidth:"80%",maxHeight:"55%",backgroundColor:"#fff",borderRadius:14,padding:14,borderWidth:1,borderColor:"#dce5de",elevation:4},
  heading:{flexDirection:"row",alignItems:"center",justifyContent:"space-between"},title:{fontSize:13,fontWeight:"700",color:"#35523f"},close:{fontSize:24,color:"#52715d",paddingHorizontal:8},
  content:{gap:7},small:{fontSize:10,lineHeight:15,color:"#748178"},score:{fontSize:27,fontWeight:"800",color:"#2d6a4f"},label:{fontSize:12,color:"#35523f"},value:{fontSize:12,color:"#35523f",marginTop:2},
  error:{fontSize:11,color:"#a05a48"},useButton:{backgroundColor:"#2d6a4f",padding:11,borderRadius:9,alignItems:"center",marginTop:5},useText:{color:"#fff",fontSize:12,fontWeight:"700"},disabled:{opacity:.4},
});
