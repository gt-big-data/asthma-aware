export interface TrendDay {date:string;value:number|null;available_hours:number;expected_hours:number}
export interface EnvironmentalTrend {
  variable:string;label:string;unit:string;days:TrendDay[];latest_value:number|null;latest_date:string|null;
  complete_days:number;change_percent:number|null;
}
export interface EnvironmentalTrends {
  source:string;timezone:string;aggregation:"daily_mean";latitude:number;longitude:number;source_grid_degrees:number;
  period_start:string;period_end:string;analysis_through:string;downloaded_at:string;analysis_lag_hours:number;stale:boolean;
  items:EnvironmentalTrend[];
}
export const TREND_VARIABLES=[{id:"no2",label:"NO₂",unit:"ppb"},{id:"so2",label:"SO₂",unit:"ppb"},{id:"pm25",label:"PM2.5",unit:"µg/m³"},{id:"o3",label:"O₃",unit:"ppb"}];
export function trendDate(date:string):string {return new Date(`${date}T12:00:00Z`).toLocaleDateString("en-US",{month:"short",day:"numeric",timeZone:"UTC"});}
export function concentration(value:number|null):string {return value===null?"—":value.toFixed(Math.abs(value)<1?2:1);}
/** Missing days keep their horizontal position and break lines; never bridge gaps. */
export function trendGeometry(days:TrendDay[],width:number,height=46) {
  const finite=days.flatMap(day=>day.value===null?[]:[day.value]);
  const low=finite.length?Math.min(...finite):0,range=finite.length?Math.max(...finite)-low:0;
  const points=days.map((day,index)=>day.value===null?null:{x:4+index/Math.max(1,days.length-1)*Math.max(0,width-8),y:range?height-5-(day.value-low)/range*(height-10):height/2});
  const segments=points.flatMap((point,index)=>index>0&&point&&points[index-1]?[{from:points[index-1]!,to:point}]:[]);
  return {points,segments};
}
