import assert from "node:assert/strict";
import test from "node:test";
import { createCellGrid, createFrameCache, createFrameLoader, nearbyFrameIndices, inForecastBounds, type MapFrameImage } from "../lib/forecast-map.ts";
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

const imageFrame = (run: string, index: number): MapFrameImage => ({
  run_id: run, index, valid_at: "2026-11-18T12:30:00Z", available: true, image: `data:image/png;base64,${index}`,
});
const flush = () => new Promise<void>(resolve => setImmediate(resolve));

test("frame cache is bounded by bytes and keeps recently displayed hours", () => {
  const frame = imageFrame("a", 1);
  const cache = createFrameCache(3, frame.image.length * 2 * 2);
  cache.put(frame);
  cache.put(imageFrame("a", 2));
  cache.get("a", 1);
  cache.put(imageFrame("a", 3));
  assert.ok(cache.get("a", 1));
  assert.equal(cache.get("a", 2), undefined);
  cache.put({ ...frame, index: 4, image: "x".repeat(1000) });
  assert.equal(cache.get("a", 4), undefined);
});

test("prefetch follows travel direction and skips missing hours", () => {
  const frames = Array.from({ length: 15 }, (_, i) => ({ index: i + 100, valid_at: "", available: i !== 7 }));
  assert.deepEqual(nearbyFrameIndices(frames, 5).slice(0, 5), [105, 106, 104, 103, 108]);
  assert.deepEqual(nearbyFrameIndices(frames, 5, -1).slice(0, 5), [105, 104, 106, 103, 102]);
  assert.ok(nearbyFrameIndices(frames, 0).every(i => i >= 100 && i < 115 && i !== 107));
});

test("loader shares inflight frames, bounds concurrency, and serves cached hours without requests", async () => {
  const pending: { run: string; index: number; resolve: (frame: MapFrameImage) => void }[] = [];
  const cache = createFrameCache();
  const loader = createFrameLoader((run, index) => new Promise(resolve => pending.push({ run, index, resolve })), cache);
  const first = loader.select("a", 1, [2, 3]);
  const duplicate = loader.select("a", 1, [2, 3]);
  await flush();
  assert.equal(first, duplicate);
  assert.deepEqual(pending.map(job => job.index), [1, 2]);
  pending[0].resolve(imageFrame("a", 1));
  assert.equal((await first).index, 1);
  await flush();
  assert.deepEqual(pending.map(job => job.index), [1, 2, 3]);
  const count = pending.length;
  assert.equal((await loader.select("a", 1, [2, 3])).index, 1);
  assert.equal(pending.length, count);
  pending[1].resolve(imageFrame("a", 2));
  pending[2].resolve(imageFrame("a", 3));
  await flush();
  loader.stop();
});

test("jumping hours cancels obsolete work and foreground requests take priority", async () => {
  const requests: { index: number; resolve: (frame: MapFrameImage) => void }[] = [];
  const loader = createFrameLoader((_run, index, signal) => new Promise((resolve, reject) => {
    requests.push({ index, resolve });
    signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
  }));
  const old = loader.select("a", 1, [2, 3]);
  const rejected = assert.rejects(old, { name: "AbortError" });
  await flush();
  const next = loader.select("a", 20, [21]);
  await flush();
  assert.deepEqual(requests.map(job => job.index), [1, 2, 20, 21]);
  requests[2].resolve(imageFrame("a", 20));
  assert.equal((await next).index, 20);
  await rejected;
  loader.stop();
});

test("late canceled responses and mismatched publications never enter the cache", async () => {
  const requests: { resolve: (frame: MapFrameImage) => void }[] = [];
  const cache = createFrameCache();
  const loader = createFrameLoader(() => new Promise(resolve => requests.push({ resolve })), cache);
  const old = loader.select("a", 1, []);
  const rejected = assert.rejects(old, { name: "AbortError" });
  await flush();
  const next = loader.select("b", 1, []);
  const invalid = assert.rejects(next, /Mismatched/);
  await flush();
  requests[0].resolve(imageFrame("a", 1));
  requests[1].resolve(imageFrame("wrong", 1));
  await rejected;
  await invalid;
  await flush();
  assert.equal(cache.get("a", 1), undefined);
  assert.equal(cache.get("b", 1), undefined);
  loader.stop();
});

test("location coverage follows the real footprint instead of the Georgia demo polygon", () => {
  const bounds: [number, number, number, number] = [-85.6, 30.78, -81.52, 34.98];
  assert.ok(inForecastBounds(33.75, -84.39, bounds));
  assert.ok(!inForecastBounds(32.08, -81.1, bounds)); // Savannah is outside today's model footprint.
  assert.ok(!inForecastBounds(30.78, -84, bounds));
});

test("fine grid edges align with the actual 420 by 408 forecast footprint", () => {
  const grid = createCellGrid([-85.6, 30.78, -81.52, 34.98], 0.01);
  assert.equal(grid.features.length, 409 + 421);
  assert.deepEqual(grid.features[0].geometry.coordinates, [[-85.6, 30.78], [-85.6, 34.98]]);
  assert.deepEqual(grid.features[408].geometry.coordinates, [[-81.52, 30.78], [-81.52, 34.98]]);
  assert.deepEqual(grid.features[409].geometry.coordinates, [[-85.6, 34.98], [-81.52, 34.98]]);
  assert.deepEqual(grid.features.at(-1)?.geometry.coordinates, [[-85.6, 30.78], [-81.52, 30.78]]);
  assert.ok(Math.abs(grid.features[1].geometry.coordinates[0][0] - grid.features[0].geometry.coordinates[0][0] - .01) < 1e-10);
});

test("cell grids retain a shifted origin and reject unaligned or excessive geometry", () => {
  const grid = createCellGrid([-84.803, 33.451, -84.773, 33.471], .01);
  assert.deepEqual(grid.features[1].geometry.coordinates, [[-84.793, 33.451], [-84.793, 33.471]]);
  assert.throws(() => createCellGrid([-85.6, 30.78, -81.52, 34.98], 0));
  assert.throws(() => createCellGrid([-85.6, 30.78, -81.52, 34.98], .07));
  assert.throws(() => createCellGrid([-85.6, 30.78, -81.52, 34.98], .00001));
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


test("physical map requests pin the selected layer without changing legacy risk URLs", async () => {
  const calls: string[] = [];
  const client = createApiClient({ baseUrl: "https://api.example.test", getAccessToken: async () => null,
    fetcher: (async (url: string | URL | Request) => {
      calls.push(String(url)); return new Response("{}", { status: 200 });
    }) as typeof fetch });
  await client.mapForecast(undefined, "temperature");
  await client.mapFrame("abc", 42, undefined, "temperature");
  await client.mapForecast();
  assert.ok(calls[0].endsWith("/v1/map/forecast?layer=temperature"));
  assert.ok(calls[1].endsWith("/v1/map/frame?run_id=abc&index=42&layer=temperature"));
  assert.ok(calls[2].endsWith("/v1/map/forecast"));
});


test("physical scrubbing finishes active work and drops superseded queued hours", async () => {
  const requests: { index: number; signal: AbortSignal; resolve: (frame: MapFrameImage) => void }[] = [];
  const cache = createFrameCache();
  const loader = createFrameLoader((run, index, signal) => new Promise(resolve => {
    requests.push({ index, signal, resolve });
  }), cache, { cancelActive: false });
  const first = loader.select("physical", 1, [2, 3, 4]);
  await flush();
  const skipped = loader.select("physical", 40, [41]);
  const rejected = assert.rejects(skipped, { name: "AbortError" });
  const latest = loader.select("physical", 80, [81]);
  await flush();
  assert.deepEqual(requests.map(item => item.index), [1, 2]);
  assert.ok(requests.every(item => !item.signal.aborted));
  requests[0].resolve(imageFrame("physical", 1));
  assert.equal((await first).index, 1);
  await flush();
  assert.deepEqual(requests.map(item => item.index), [1, 2, 80]);
  requests[1].resolve(imageFrame("physical", 2));
  await flush();
  requests.find(item => item.index === 80)!.resolve(imageFrame("physical", 80));
  assert.equal((await latest).index, 80);
  await rejected;
  assert.equal(cache.get("physical", 1)?.index, 1);
  assert.equal(cache.get("physical", 40), undefined);
  loader.stop();
});


test("personal maps use authenticated routes and never silently fall back to general risk", async () => {
  const paths: string[] = [];
  const api = createApiClient({ baseUrl: "https://api.example", getAccessToken: async () => "user-token",
    fetcher: async (url, options) => {
      paths.push(String(url));
      assert.equal((options?.headers as Record<string,string>).Authorization, "Bearer user-token");
      return new Response("{}", { status: 200 });
    } });
  await api.mapForecast(undefined, "personal");
  await api.mapFrame("personal-id", 50, undefined, "personal");
  assert.deepEqual(paths, ["https://api.example/v1/map/personal/forecast", "https://api.example/v1/map/personal/frame?run_id=personal-id&index=50"]);
  const guest = createApiClient({ baseUrl: "https://api.example", getAccessToken: async () => null,
    fetcher: async () => { assert.fail("Anonymous requests must not be sent"); } });
  await assert.rejects(guest.mapForecast(undefined, "personal"), error => error instanceof ApiError && error.status === 401);
});
