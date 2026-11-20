import assert from "node:assert/strict";
import test from "node:test";
import { createApiClient } from "../lib/api-client.ts";
import { coordinatePlace, placeLabel, fromSavedPlace, parseSelection, selectionKey } from "../lib/saved-locations.ts";

test("selection storage separates accounts and rejects unknown or malformed city identifiers", () => {
  assert.notEqual(selectionKey("user-a"), selectionKey("user-b"));
  assert.notEqual(selectionKey("user-a"), selectionKey());
  assert.deepEqual(parseSelection('{"id":"1304000","label":"Home"}'), { id: "1304000", label: "Home" });
  for (const text of [null, "invalid", '{"id":"3651000","label":"NY"}', '{"id":"1304000","label":"  "}']) assert.equal(parseSelection(text), null);
});

test("saved place conversion preserves canonical coordinates and coverage", () => {
  const city = fromSavedPlace({ city_id: "1304000", label: "Home", city_name: "Atlanta", latitude: 33.76, longitude: -84.42, state: "GA", forecast_supported: true });
  assert.equal(city.id, "1304000");
  assert.equal(city.name, "Atlanta");
  assert.equal(city.label, "Home");
  assert.equal(city.latitude, 33.76);
});

test("city search is public; save and delete use current authentication and DELETE accepts 204", async () => {
  const calls: { url: string; method: string; auth?: string }[] = [];
  const api = createApiClient({ baseUrl: "https://api.example", getAccessToken: async () => "latest-token",
    fetcher: async (url, options) => {
      calls.push({ url: String(url), method: options?.method ?? "GET", auth: (options?.headers as Record<string, string>).Authorization });
      return options?.method === "DELETE" ? new Response(null, { status: 204 }) : new Response('{"items":[]}', { status: 200 });
    } });
  await api.searchCities("Athens & Atlanta");
  await api.saveLocation("1304000", "Home");
  assert.equal(await api.deleteLocation("1304000"), undefined);
  assert.equal(calls[0].auth, undefined);
  assert.ok(calls[0].url.endsWith("Athens%20%26%20Atlanta"));
  assert.equal(calls[1].auth, "Bearer latest-token");
  assert.equal(calls[2].method, "DELETE");
});


test("coordinate preferences preserve exact location identity and validate stored input",()=>{
  const place=coordinatePlace(33.755,-84.385,true);
  assert.equal(place.id,"coordinates:33.75500:-84.38500");
  assert.notEqual(place.id,coordinatePlace(33.765,-84.385,true).id);
  assert.equal(placeLabel(place),"33.75500, -84.38500 · GA");
  const stored={kind:"coordinates",latitude:place.latitude,longitude:place.longitude,label:place.label};
  assert.deepEqual(parseSelection(JSON.stringify(stored)),stored);
  for(const bad of [{...stored,latitude:91},{...stored,latitude:"33.75"},{...stored,longitude:null}]) assert.equal(parseSelection(JSON.stringify(bad)),null);
  assert.throws(()=>coordinatePlace(NaN,-84,true));
});


test("coordinate header labels always use the selected point even if a stale city name remains",()=>{
  const point=coordinatePlace(33.755,-84.375,true);
  assert.equal(placeLabel({...point,name:"Atlanta",label:"Atlanta"}),"33.75500, -84.37500 · GA");
  assert.equal(placeLabel({...point,name:"Atlanta",label:"Home"}),"Home · 33.75500, -84.37500");
});
