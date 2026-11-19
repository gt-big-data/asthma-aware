import { useEffect, useState } from "react";
import { AccessibilityInfo, ActivityIndicator, Animated, Easing, Platform, StyleSheet, Text, View, type DimensionValue } from "react-native";

function useLoadingPulse() {
  // Stay still until the user's motion preference has been checked.
  const [reducedMotion,setReducedMotion]=useState(true);
  const [opacity]=useState(()=>new Animated.Value(.6));
  useEffect(()=>{
    let active=true;
    void AccessibilityInfo.isReduceMotionEnabled().then(value=>{if(active)setReducedMotion(value);}).catch(()=>{});
    const subscription=AccessibilityInfo.addEventListener("reduceMotionChanged",setReducedMotion);
    return ()=>{active=false;subscription.remove();};
  },[]);
  useEffect(()=>{
    if(reducedMotion){opacity.setValue(.6);return;}
    const animation=Animated.loop(Animated.sequence([
      Animated.timing(opacity,{toValue:.95,duration:850,easing:Easing.inOut(Easing.ease),useNativeDriver:Platform.OS!=="web",isInteraction:false}),
      Animated.timing(opacity,{toValue:.4,duration:850,easing:Easing.inOut(Easing.ease),useNativeDriver:Platform.OS!=="web",isInteraction:false}),
    ]));
    animation.start();
    return ()=>animation.stop();
  },[opacity,reducedMotion]);
  return {opacity,reducedMotion};
}

export function LoadingSkeleton({width="100%",height=12}:{width?:DimensionValue;height?:number}) {
  const {opacity}=useLoadingPulse();
  return <Animated.View accessible={false} pointerEvents="none" style={[styles.block,{opacity,width,height}]}/>;
}

export default function LoadingFeedback({label,skeleton=false,compact=false,color="#2d6a4f"}:{label:string;skeleton?:boolean;compact?:boolean;color?:string}) {
  const {opacity,reducedMotion}=useLoadingPulse();
  return <View accessibilityRole="progressbar" accessibilityLabel={label} accessibilityState={{busy:true}}
    accessibilityLiveRegion="polite" style={[styles.root,skeleton&&styles.reserved]}>
    <View style={styles.row}>
      {reducedMotion?<View accessible={false} style={[styles.staticDot,{backgroundColor:color}]}/>:<ActivityIndicator accessible={false} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden size="small" color={color}/>}
      {!compact&&<Text style={styles.label}>{label}</Text>}
    </View>
    {skeleton&&<Animated.View accessible={false} pointerEvents="none" style={[styles.lines,{opacity}]}>
      <View style={[styles.block,{height:22,width:"48%"}]}/>
      <View style={[styles.block,{height:11,width:"82%"}]}/>
      <View style={[styles.block,{height:11,width:"62%"}]}/>
    </Animated.View>}
  </View>;
}
const styles=StyleSheet.create({
  root:{gap:12},reserved:{minHeight:104,justifyContent:"center"},row:{flexDirection:"row",alignItems:"center",gap:9,minHeight:22},
  label:{fontSize:11,lineHeight:17,color:"#66766b",flexShrink:1},staticDot:{height:10,width:10,borderRadius:5,backgroundColor:"#6a8c77",margin:5},
  block:{borderRadius:6,backgroundColor:"#dce8df"},lines:{gap:9},
});
