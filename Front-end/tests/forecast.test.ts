import test from "node:test";
import assert from "node:assert/strict";
import { forecastHours, forecastPixels, parseForecast, samePoint } from "../lib/forecast.ts";
import { ApiError, createApiClient } from "../lib/api-client.ts";

function fixture() {
  return {
    source: "NASA GEOS-CF v2", run_id: "20261115_09z", initialized_at: "2026-11-15T09:00:00Z",
    valid_until: "2026-11-15T14:00:00Z", method: "native-cell-copy; equal-weight-v1", placeholder: true as const,
    notice: "Development score", location: {lat:33.75, lon:-84.39, pixel_id:"example", native_lat:33.75, native_lon:-84.5, display_resolution_km:1},
    units: {pm25:"ug m-3",no2:"ppb",o3:"ppb"},
    grid: {south:33.625,north:33.875,west:-84.625,east:-84.375,rows:28,cols:24,lat_step:.25/28,lon_step:.25/24},
    hourly: [12,13].map((hour) => ({timestamp:`2026-11-15T${hour}:00:00Z`,values:{pm25:25,no2:50,o3:50},score:50})),
  };
}

test("forecast parsing requires complete finite values and source provenance", () => {
  const value = fixture();
  assert.equal(parseForecast(value), value);
  for (const malformed of [null, {}, {...value, source:"sample"}, {...value, hourly:[]},
    {...value, grid:{...value.grid,rows:100000}}, {...value, units:{pm25:"ppb"}},
    {...value, hourly:[{...value.hourly[0],values:{pm25:null,no2:50,o3:50}}]},
    {...value, hourly:[{...value.hourly[0],score:NaN}]}]) {
    assert.throws(() => parseForecast(malformed));
  }
});

test("forecast pixel replication preserves score and tiles the footprint without overshoot", () => {
  const value = parseForecast(fixture());
  const pixels = forecastPixels(value,value.hourly[0]);
  assert.equal(pixels.length,672);
  assert(pixels.every((p) => p[2] === .5 && p[0] <= value.grid.north && p[0] > value.grid.south
    && p[1] >= value.grid.west && p[1] < value.grid.east));
  assert.deepEqual(pixels[0], [value.grid.north,value.grid.west,.5]);
  const last = pixels.at(-1)!;
  assert(Math.abs(last[0]-value.grid.lat_step-value.grid.south)<1e-9);
  assert(Math.abs(last[1]+value.grid.lon_step-value.grid.east)<1e-9);
});

test("displayed hours expire without inventing replacement values", () => {
  const value = parseForecast(fixture());
  assert.equal(forecastHours(value,Date.parse("2026-11-15T12:30:00Z")).length,2);
  assert.equal(forecastHours(value,Date.parse("2026-11-15T13:00:00Z"))[0].timestamp,value.hourly[1].timestamp);
  assert.deepEqual(forecastHours(value,Date.parse(value.valid_until)),[]);
  assert.deepEqual(forecastHours(value,Date.parse("2026-11-15T11:00:00Z")),[]);
  assert.deepEqual(forecastHours(value,Date.parse("2026-11-18T13:00:00Z")),[]);
});

test("authenticated forecast requests use real coordinates and parse the response", async () => {
  let calls=0;
  const client=createApiClient({baseUrl:"https://api.example/",getAccessToken:async()=>"current-token",
    fetcher:async(url,options)=>{
      calls+=1;
      assert.equal(String(url),"https://api.example/v1/forecast?lat=33.75&lon=-84.39");
      assert.equal((options?.headers as Record<string,string>).Authorization,"Bearer current-token");
      return new Response(JSON.stringify(fixture()));
    }});
  assert.equal((await client.forecast({latitude:33.75,longitude:-84.39})).hourly[0].score,50);
  assert.equal(calls,1);
});

test("unavailable and cancelled forecasts never become sample values or repeated requests", async () => {
  let calls=0;
  const client=createApiClient({baseUrl:"https://api.example",getAccessToken:async()=>"token",fetcher:async()=>{
    calls+=1; return new Response("{}",{status:503});
  }});
  await assert.rejects(client.forecast({latitude:33.75,longitude:-84.39}),ApiError);
  const controller=new AbortController(); controller.abort();
  await assert.rejects(client.forecast({latitude:33.75,longitude:-84.39},controller.signal),ApiError);
  assert.equal(calls,1);
  assert(samePoint({latitude:0,longitude:0},{latitude:0,longitude:0}));
  assert(!samePoint(null,{latitude:0,longitude:0}));
});

test("session lookup is included in the API timeout and cancellation budget", async () => {
  let calls=0;
  const client=createApiClient({baseUrl:"https://api.example",timeoutMs:5,
    getAccessToken:()=>new Promise(()=>{}), fetcher:async()=>{calls+=1;return new Response("{}");}});
  await assert.rejects(client.forecast({latitude:33.75,longitude:-84.39}),ApiError);
  assert.equal(calls,0);
});
