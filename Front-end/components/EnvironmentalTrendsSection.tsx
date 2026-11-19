import LoadingFeedback, { LoadingSkeleton } from "./LoadingFeedback";
import {Ionicons} from "@expo/vector-icons";
import {useState} from "react";
import {StyleSheet,Text,TouchableOpacity,View} from "react-native";
import {concentration,trendDate,trendGeometry,TREND_VARIABLES,type EnvironmentalTrends,type TrendDay} from "../lib/environmental-trends";

function Sparkline({days,color}:{days:TrendDay[];color:string}) {
  const [width,setWidth]=useState(0);
  const {points,segments}=trendGeometry(days,width);
  return <View accessible={false} style={styles.sparkline} onLayout={event=>setWidth(event.nativeEvent.layout.width)}>
    {segments.map(({from,to},index)=>{const dx=to.x-from.x,dy=to.y-from.y,length=Math.hypot(dx,dy);return <View key={index} style={[styles.segment,{left:(from.x+to.x-length)/2,top:(from.y+to.y-2)/2,width:length,backgroundColor:color,transform:[{rotate:`${Math.atan2(dy,dx)}rad`}]}]}/>;})}
    {points.map((point,index)=>point?<View key={index} style={[styles.dot,{left:point.x-3,top:point.y-3,backgroundColor:color}]}/>:null)}
  </View>;
}
export default function EnvironmentalTrendsSection({data,loading,onRetry}:{data:EnvironmentalTrends|null;loading:boolean;onRetry:()=>void}) {
  const [expanded,setExpanded]=useState<string|null>(null);
  return <View style={styles.section}>
    <View style={styles.heading}><View style={styles.flex}><Text style={styles.title}>Environmental trends</Text>
      <Text style={styles.subtitle}>{data?`Daily means · ${trendDate(data.period_start)}–${trendDate(data.period_end)}`:"Past 7 completed days · Daily means"}</Text></View>
      <Ionicons name="analytics-outline" size={20} color="#66766b"/>
    </View>
    <View style={styles.grid}>{TREND_VARIABLES.map(variable=>{
      const item=data?.items.find(item=>item.variable===variable.id);
      const change=item?.change_percent??null;
      const color=change===null||Math.abs(change)<.05?"#66766b":change>0?"#ad5948":"#2d6a4f";
      return <TouchableOpacity key={variable.id} accessibilityRole="button" accessibilityLabel={`${variable.label} daily analysis values`}
        disabled={loading} accessibilityState={{expanded:expanded===variable.id,busy:loading}} onPress={()=>setExpanded(value=>value===variable.id?null:variable.id)} style={styles.card}>
        <View style={styles.cardHeading}><Text style={styles.name}>{variable.label}</Text><Ionicons name={change===null?"remove-outline":change>0?"arrow-up-outline":change<0?"arrow-down-outline":"remove-outline"} size={16} color={color}/></View>
        <View style={styles.valueRow}>{loading?<LoadingSkeleton width={65} height={25}/>:<Text style={styles.value}>{concentration(item?.latest_value??null)}</Text>}<Text style={styles.unit}>{variable.unit}</Text></View>
        <Text style={styles.small}>{item?.latest_date?`${trendDate(item.latest_date)} · Daily mean`:loading?"Loading analysis…":"No complete daily mean"}</Text>
        {loading?<View style={styles.sparkline}><LoadingFeedback label={`Loading ${variable.label} analysis`}/></View>:<Sparkline days={item?.days??[]} color={color}/>}
        {data&&<View style={styles.dates}><Text style={styles.tiny}>{trendDate(data.period_start)}</Text><Text style={styles.tiny}>{trendDate(data.period_end)}</Text></View>}
        <Text style={[styles.change,{color}]}>{loading?"Loading comparison…":change===null?"Change unavailable":`${change>0?"+":""}${change.toFixed(1)}% vs ${trendDate(data!.period_start)}`}</Text>
        <Text style={styles.small}>{item?`${item.complete_days}/7 complete days · Tap for values`:"Hourly NASA analysis"}</Text>
      </TouchableOpacity>;
    })}</View>
    {data&&expanded&&<View style={styles.details}>{data.items.find(item=>item.variable===expanded)?.days.map(day=><View key={day.date} style={styles.detailRow}>
      <Text style={styles.small}>{trendDate(day.date)}</Text><Text style={styles.small}>{day.value===null?`Incomplete · ${day.available_hours}/${day.expected_hours}h`: `${concentration(day.value)} ${data.items.find(item=>item.variable===expanded)!.unit} · ${day.available_hours}/${day.expected_hours}h`}</Text>
    </View>)}</View>}
    {data?<Text style={styles.note}>NASA GEOS-CF analysis estimates · Native 0.25° data{data.stale?" · Delayed analysis":""}{"\n"}Analysis through {new Date(data.analysis_through).toLocaleString("en-US",{timeZone:data.timezone,month:"short",day:"numeric",hour:"numeric",minute:"2-digit",timeZoneName:"short"})} · {data.analysis_lag_hours.toFixed(0)}h behind now. Gaps indicate incomplete days.</Text>
      :!loading&&<TouchableOpacity onPress={onRetry} accessibilityRole="button" accessibilityLabel="Retry environmental trends"><Text style={styles.note}>Historical analysis unavailable. Tap to retry.</Text></TouchableOpacity>}
  </View>;
}
const styles=StyleSheet.create({
  section:{gap:10},heading:{flexDirection:"row",justifyContent:"space-between",alignItems:"center"},flex:{flex:1},title:{fontSize:18,fontWeight:"700",color:"#25352c"},subtitle:{fontSize:11,color:"#66766b",marginTop:4},
  grid:{flexDirection:"row",flexWrap:"wrap",gap:10},card:{flexGrow:1,flexBasis:"43%",minWidth:130,backgroundColor:"#fff",borderColor:"#dce5de",borderWidth:1,borderRadius:18,padding:13},
  cardHeading:{flexDirection:"row",justifyContent:"space-between"},name:{fontSize:12,fontWeight:"600",color:"#66766b"},valueRow:{flexDirection:"row",alignItems:"baseline",gap:4,marginTop:4},value:{fontSize:21,fontWeight:"700",color:"#1f2d25"},unit:{fontSize:10,color:"#66766b"},
  small:{fontSize:10,lineHeight:16,color:"#66766b"},tiny:{fontSize:9,color:"#7b8b80"},dates:{flexDirection:"row",justifyContent:"space-between"},change:{fontSize:10,fontWeight:"700",marginTop:5},
  sparkline:{height:46,marginTop:7,overflow:"hidden"},segment:{position:"absolute",height:2.5,borderRadius:2},dot:{position:"absolute",height:6,width:6,borderRadius:3},
  note:{fontSize:10,lineHeight:16,color:"#66766b"},details:{padding:12,borderRadius:12,backgroundColor:"#fff",gap:5},detailRow:{flexDirection:"row",justifyContent:"space-between",gap:12},
});
