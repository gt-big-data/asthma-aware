import assert from "node:assert/strict";
import test from "node:test";
import {trendGeometry,concentration,type TrendDay} from "../lib/environmental-trends.ts";
import {createApiClient} from "../lib/api-client.ts";

test("historical charts preserve calendar gaps rather than drawing across missing days",()=>{
  const days=[1,2,null,3,3,null,4].map((value,index)=>({date:`2026-11-${12+index}`,value,available_hours:value===null?12:24,expected_hours:24}));
  const {points,segments}=trendGeometry(days,128);
  assert.equal(points.length,7);assert.equal(points[2],null);assert.equal(points[5],null);
  assert.equal(segments.length,2);assert.equal(points[0]!.x,4);assert.equal(points[6]!.x,124);
  assert.equal(concentration(null),"—");assert.equal(concentration(.1234),"0.12");
});
test("flat and empty histories remain finite and don't invent increases",()=>{
  const day:TrendDay={date:"2026-11-12",value:4,available_hours:24,expected_hours:24};
  const plot=trendGeometry([day,day],100);
  assert.equal(plot.points[0]!.y,plot.points[1]!.y);
  assert.deepEqual(trendGeometry([],100),{points:[],segments:[]});
});
test("historical data requests use the selected coordinates and remain public",async()=>{
  const calls:string[]=[];
  const api=createApiClient({baseUrl:"https://api.example",getAccessToken:async()=>null,fetcher:async(url,options)=>{
    calls.push(String(url));assert.equal((options?.headers as Record<string,string>).Authorization,undefined);
    return new Response("{}",{status:200});
  }});
  await api.environmentalTrends(33.755,-84.385);
  assert.equal(calls[0],"https://api.example/v1/dashboard/environmental-trends?lat=33.755&lon=-84.385");
});
