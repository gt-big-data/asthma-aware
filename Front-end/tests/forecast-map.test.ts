import assert from "node:assert/strict";
import test from "node:test";
import { createFrameCache, inForecastBounds } from "../lib/forecast-map.ts";
import { createApiClient, ApiError } from "../lib/api-client.ts";

test("map cache separates publications and evicts old frames", () => {
  const cache = createFrameCache(2);
  const frame = { run_id: "a", index: 1, valid_at: "2026-11-18T12:30:00Z", available: true, image: "data:image/png;base64,test" };
  cache.put(frame);
  cache.put({ ...frame, run_id: "b" });
  assert.equal(cache.get("a", 1)?.run_id, "a");
  assert.equal(cache.get("b", 1)?.run_id, "b");
  cache.put({ ...frame, run_id: "c" });
  assert.equal(cache.get("a", 1), undefined);
  assert.equal(cache.get("c", 1)?.run_id, "c");
});

test("location coverage follows the real footprint instead of the Georgia demo polygon", () => {
  const bounds: [number, number, number, number] = [-85.6, 30.78, -81.52, 34.98];
  assert.ok(inForecastBounds(33.75, -84.39, bounds));
  assert.ok(!inForecastBounds(32.08, -81.1, bounds)); // Savannah is outside today's model footprint.
  assert.ok(!inForecastBounds(30.78, -84, bounds));
});

test("public map calls pin frames to a run and never request a user token", async () => {
  const paths: string[] = [];
  const api = createApiClient({ baseUrl: "https://api.example", getAccessToken: async () => { throw new Error("Map should be public"); },
    fetcher: async (url, options) => {
      paths.push(String(url));
      assert.equal((options?.headers as Record<string, string>).Authorization, undefined);
      return new Response("{}", { status: 200 });
    } });
  await api.mapForecast();
  await api.mapFrame("a".repeat(64), 52);
  assert.deepEqual(paths, ["https://api.example/v1/map/forecast", `https://api.example/v1/map/frame?run_id=${"a".repeat(64)}&index=52`]);
});

test("obsolete map requests can be aborted and run changes remain identifiable", async () => {
  const controller = new AbortController();
  let observed: AbortSignal | null | undefined;
  const api = createApiClient({ baseUrl: "https://api.example", getAccessToken: async () => null,
    fetcher: async (_url, options) => {
      observed = options?.signal;
      controller.abort();
      assert.ok(observed?.aborted);
      return new Response("{}", { status: 409 });
    } });
  await assert.rejects(api.mapFrame("a".repeat(64), 52, controller.signal), error => error instanceof ApiError && error.status === 409);
});
